"""SQLite FTS5 retrieval benchmark for issue #27 approved-source fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import resource
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .runtime_policy import load_runtime_policy


BENCHMARK_ID = "mvp-retrieval-benchmark-issue-27"
DENSE_BENCHMARK_ID = "mvp-dense-retrieval-benchmark-issue-28"
DENSE_INDEX_SCHEMA_VERSION = "dense-retrieval-index-v1"
DENSE_INDEX_ENGINE = "local-dense-json"
DEFAULT_CORPUS_PATH = "data/retrieval_benchmark/corpus-fixtures.json"
DEFAULT_LEXICAL_QUERIES_PATH = "data/retrieval_benchmark/evaluation-queries.json"
DEFAULT_DENSE_QUERIES_PATH = "data/retrieval_benchmark/dense-evaluation-queries.json"
DEFAULT_LEXICAL_OUTPUT_PATH = "docs/progress/issue-27-retrieval-benchmark.json"
DEFAULT_DENSE_OUTPUT_PATH = "docs/progress/issue-28-dense-retrieval-benchmark.json"
DEFAULT_DENSE_INDEX_PATH = "docs/progress/issue-28-dense-index.json"
APPROVAL_STATES = {"approved", "unapproved"}
SOURCE_HEALTH_STATES = {
    "healthy",
    "overdue-policy-usable",
    "changed-unreviewed",
    "broken",
    "extraction-failed",
}
BLOCKED_SOURCE_HEALTH = {"changed-unreviewed", "broken", "extraction-failed"}
POLICY_USABLE_OVERDUE_HEALTH = "overdue-policy-usable"
FIXTURE_ID_PATTERN = re.compile(r"^di-rag-doc-[a-z0-9-]+$")
QUERY_ID_PATTERN = re.compile(r"^di-rag-query-[a-z0-9-]+$")
TOKEN_PATTERN = re.compile(r"[0-9a-zA-ZæøåÆØÅ]+")


class BenchmarkValidationError(ValueError):
    """Raised when benchmark fixtures or queries are invalid."""


class DenseIndexCompatibilityError(BenchmarkValidationError):
    """Raised when an existing dense index cannot be queried safely."""


class OllamaEmbeddingClient:
    def __init__(self, endpoint: str, timeout_seconds: float = 60.0):
        self.endpoint = endpoint.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def show_model(self, model: str) -> dict[str, Any]:
        try:
            return self._request("POST", "/api/show", {"model": model})
        except urllib.error.HTTPError as exc:
            if exc.code in {400, 404}:
                raise FileNotFoundError(model) from exc
            raise

    def embed(self, model: str, text: str) -> dict[str, Any]:
        return self._request("POST", "/api/embed", {"model": model, "input": text})

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            f"{self.endpoint}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


def load_benchmark_inputs(
    corpus_path: str | Path, queries_path: str | Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    corpus_file = Path(corpus_path)
    queries_file = Path(queries_path)
    fixtures = _load_json_list(corpus_file, "corpus fixtures")
    queries = _load_json_list(queries_file, "evaluation queries")
    _validate_fixtures(fixtures)
    _validate_queries(queries, {fixture["id"] for fixture in fixtures})
    return fixtures, queries


def build_fts_index(fixtures: list[dict[str, Any]]) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            publisher TEXT NOT NULL,
            official_url TEXT NOT NULL,
            topic_tags TEXT NOT NULL,
            language TEXT NOT NULL,
            approval_state TEXT NOT NULL,
            source_health TEXT NOT NULL,
            checked_at_utc TEXT NOT NULL,
            content TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE VIRTUAL TABLE documents_fts
        USING fts5(id UNINDEXED, content, tokenize = 'unicode61')
        """
    )
    for fixture in fixtures:
        connection.execute(
            """
            INSERT INTO documents (
                id,
                publisher,
                official_url,
                topic_tags,
                language,
                approval_state,
                source_health,
                checked_at_utc,
                content
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fixture["id"],
                fixture["publisher"],
                fixture["official_url"],
                json.dumps(fixture["topic_tags"], sort_keys=True),
                fixture["language"],
                fixture["approval_state"],
                fixture["source_health"],
                fixture["checked_at_utc"],
                fixture["content"],
            ),
        )
        connection.execute(
            "INSERT INTO documents_fts(id, content) VALUES (?, ?)",
            (fixture["id"], fixture["content"]),
        )
    return connection


def run_retrieval_benchmark(
    corpus_path: str | Path,
    queries_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    fixtures, queries = load_benchmark_inputs(corpus_path, queries_path)
    connection = build_fts_index(fixtures)
    fixtures_by_id = {fixture["id"]: fixture for fixture in fixtures}
    query_results: list[dict[str, Any]] = []
    try:
        for query in queries:
            query_results.append(_evaluate_query(connection, fixtures_by_id, query))
    finally:
        connection.close()

    result = {
        "benchmark_id": BENCHMARK_ID,
        "executed_at_utc": started.isoformat(),
        "fixture_identity": {
            "corpus_path": str(corpus_path),
            "corpus_sha256": _sha256_file(corpus_path),
            "queries_path": str(queries_path),
            "queries_sha256": _sha256_file(queries_path),
            "document_ids": [fixture["id"] for fixture in fixtures],
            "query_ids": [query["id"] for query in queries],
        },
        "index": {
            "engine": "sqlite-fts5",
            "document_count": len(fixtures),
        },
        "summary": _summarize_results(query_results),
        "queries": query_results,
    }
    if output_path is not None:
        write_benchmark_result(result, output_path)
    return result


def run_dense_retrieval_benchmark(
    corpus_path: str | Path,
    queries_path: str | Path,
    *,
    index_path: str | Path | None = None,
    output_path: str | Path | None = None,
    policy_path: str | Path = "config/runtime-policy.json",
    embedding_endpoint: str | None = None,
    embedding_model: str | None = None,
    client: Any | None = None,
    rebuild_index: bool = True,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    fixtures, queries = load_benchmark_inputs(corpus_path, queries_path)
    fixture_identity = _fixture_identity(corpus_path, queries_path, fixtures, queries)
    corpus_identity = _corpus_fixture_identity(corpus_path, fixtures)

    policy: dict[str, Any] | None = None
    if client is None or embedding_model is None:
        policy = load_runtime_policy(policy_path)
    if embedding_model is None:
        embedding_model = policy["models"]["embedding"]["provisional_candidate"]  # type: ignore[index]
    if client is None:
        endpoint = embedding_endpoint or policy["providers"]["initial"]["default_endpoint"]  # type: ignore[index]
        client = OllamaEmbeddingClient(endpoint)

    model_identity = _inspect_embedding_model(client, embedding_model)
    operations = _empty_dense_operations()

    if rebuild_index:
        index, build_operations = _build_dense_index(
            fixtures,
            corpus_identity=corpus_identity,
            client=client,
            embedding_model=embedding_model,
            model_identity=model_identity,
            index_path=index_path,
        )
        operations.update(build_operations)
    else:
        if index_path is None:
            raise DenseIndexCompatibilityError(
                "Dense index path is required when --reuse-index is used. Re-index required."
            )
        index = _load_compatible_dense_index(
            index_path,
            expected_corpus_identity=corpus_identity,
            embedding_model=embedding_model,
            model_identity=model_identity,
            client=client,
        )
        operations["dense_index_size_bytes"] = Path(index_path).stat().st_size

    fixtures_by_id = {fixture["id"]: fixture for fixture in fixtures}
    query_results: list[dict[str, Any]] = []
    vectors_by_id = {
        item["id"]: item["vector"] for item in index["vectors"] if isinstance(item, dict)
    }
    for query in queries:
        query_results.append(
            _evaluate_dense_query(
                fixtures_by_id,
                vectors_by_id,
                query,
                client=client,
                embedding_model=embedding_model,
                operations=operations,
            )
        )

    result = {
        "benchmark_id": DENSE_BENCHMARK_ID,
        "executed_at_utc": started.isoformat(),
        "fixture_identity": fixture_identity,
        "index": index["metadata"],
        "operations": _finalize_dense_operations(operations),
        "summary": _summarize_results(query_results),
        "queries": query_results,
    }
    if output_path is not None:
        write_benchmark_result(result, output_path)
    return result


def write_benchmark_result(result: dict[str, Any], path: str | Path) -> None:
    _write_json_atomic(result, path)


def write_benchmark_failure(
    error: Exception, path: str | Path, *, benchmark_id: str = BENCHMARK_ID
) -> None:
    failure = {
        "benchmark_id": benchmark_id,
        "exit_status": 1,
        "diagnostic": str(error),
        "failed_at_utc": datetime.now(UTC).isoformat(),
    }
    _write_json_atomic(failure, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("lexical", "dense"),
        default="lexical",
        help="Run the issue #27 lexical benchmark or the issue #28 dense benchmark.",
    )
    parser.add_argument(
        "--corpus",
        default=DEFAULT_CORPUS_PATH,
        help="Path to the project-authored corpus fixture JSON.",
    )
    parser.add_argument(
        "--queries",
        default=None,
        help="Path to the reviewed evaluation-query JSON. Defaults depend on --mode.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path where complete benchmark results are written atomically.",
    )
    parser.add_argument(
        "--failure-output",
        default=None,
        help="Path where failed benchmark diagnostics are written atomically.",
    )
    parser.add_argument(
        "--policy",
        default="config/runtime-policy.json",
        help="Path to the runtime policy JSON used for dense embedding defaults.",
    )
    parser.add_argument(
        "--embedding-endpoint",
        default=None,
        help="Loopback embedding endpoint. Defaults to the runtime policy provider endpoint.",
    )
    parser.add_argument(
        "--embedding-model",
        default=None,
        help="Embedding model. Defaults to the runtime policy provisional candidate.",
    )
    parser.add_argument(
        "--index",
        default=DEFAULT_DENSE_INDEX_PATH,
        help="Dense benchmark index path.",
    )
    parser.add_argument(
        "--reuse-index",
        action="store_true",
        help="Validate and query an existing dense index instead of rebuilding it.",
    )
    parser.add_argument(
        "--timeout",
        default=60.0,
        type=float,
        help="Timeout in seconds for each dense embedding HTTP request.",
    )
    args = parser.parse_args(argv)

    queries_path = args.queries or (
        DEFAULT_DENSE_QUERIES_PATH if args.mode == "dense" else DEFAULT_LEXICAL_QUERIES_PATH
    )
    output_path = args.output or (
        DEFAULT_DENSE_OUTPUT_PATH if args.mode == "dense" else DEFAULT_LEXICAL_OUTPUT_PATH
    )
    failure_output = args.failure_output or output_path.replace(".json", ".failed.json")
    benchmark_id = DENSE_BENCHMARK_ID if args.mode == "dense" else BENCHMARK_ID

    try:
        if args.mode == "dense":
            policy = load_runtime_policy(args.policy)
            endpoint = args.embedding_endpoint or policy["providers"]["initial"]["default_endpoint"]
            client = OllamaEmbeddingClient(endpoint, timeout_seconds=args.timeout)
            result = run_dense_retrieval_benchmark(
                args.corpus,
                queries_path,
                index_path=args.index,
                output_path=output_path,
                policy_path=args.policy,
                embedding_endpoint=endpoint,
                embedding_model=args.embedding_model,
                client=client,
                rebuild_index=not args.reuse_index,
            )
        else:
            result = run_retrieval_benchmark(args.corpus, queries_path, output_path=output_path)
    except BenchmarkValidationError as exc:
        write_benchmark_failure(exc, failure_output, benchmark_id=benchmark_id)
        print(f"Benchmark validation failed: {exc}", file=sys.stderr)
        print(f"Failure evidence: {failure_output}", file=sys.stderr)
        return 1

    label = "Dense retrieval" if args.mode == "dense" else "Retrieval"
    print(
        f"{label} benchmark complete: "
        f"Recall@1={result['summary']['recall_at_1']:.3f}, "
        f"Recall@3={result['summary']['recall_at_3']:.3f}, "
        f"MRR={result['summary']['mean_reciprocal_rank']:.3f}"
    )
    print(f"Evidence: {output_path}")
    return 0


def _evaluate_query(
    connection: sqlite3.Connection,
    fixtures_by_id: dict[str, dict[str, Any]],
    query: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    rows = connection.execute(
        """
        SELECT documents.*, bm25(documents_fts) AS rank
        FROM documents_fts
        JOIN documents ON documents.id = documents_fts.id
        WHERE documents_fts MATCH ?
        ORDER BY rank
        LIMIT 10
        """,
        (_fts_match_expression(query["query_text"]),),
    ).fetchall()
    latency_ms = round((time.perf_counter() - started) * 1000, 3)

    raw_result_ids = [row["id"] for row in rows]
    return _evaluate_ranked_result_ids(
        fixtures_by_id,
        query,
        raw_result_ids,
        latency_ms=latency_ms,
    )


def _evaluate_dense_query(
    fixtures_by_id: dict[str, dict[str, Any]],
    vectors_by_id: dict[str, list[float]],
    query: dict[str, Any],
    *,
    client: Any,
    embedding_model: str,
    operations: dict[str, Any],
) -> dict[str, Any]:
    embedding_started = time.perf_counter()
    query_vector, payload = _embed_text(
        client,
        embedding_model,
        query["query_text"],
        f"{query['id']} query_text",
    )
    embedding_latency_ms = round((time.perf_counter() - embedding_started) * 1000, 3)
    _record_embedding_payload(operations, payload)

    similarity_started = time.perf_counter()
    scored = [
        (fixture_id, _cosine_similarity(query_vector, vector))
        for fixture_id, vector in vectors_by_id.items()
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    raw_result_ids = [fixture_id for fixture_id, _score in scored[:10]]
    similarity_latency_ms = round((time.perf_counter() - similarity_started) * 1000, 3)
    latency_ms = round(embedding_latency_ms + similarity_latency_ms, 3)

    return _evaluate_ranked_result_ids(
        fixtures_by_id,
        query,
        raw_result_ids,
        latency_ms=latency_ms,
        extra={
            "embedding_latency_ms": embedding_latency_ms,
            "warm_similarity_latency_ms": similarity_latency_ms,
            "embedding_load_duration_ms": _duration_ms(payload.get("load_duration")),
        },
    )


def _evaluate_ranked_result_ids(
    fixtures_by_id: dict[str, dict[str, Any]],
    query: dict[str, Any],
    raw_result_ids: list[str],
    *,
    latency_ms: float,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    eligible_result_ids: list[str] = []
    blocked_result_ids: list[str] = []
    overdue_result_ids: list[str] = []
    for result_id in raw_result_ids:
        fixture = fixtures_by_id[result_id]
        if _is_eligible_for_query(fixture, query):
            eligible_result_ids.append(fixture["id"])
            if fixture["source_health"] == POLICY_USABLE_OVERDUE_HEALTH:
                overdue_result_ids.append(fixture["id"])
        elif _is_blocked_source(fixture):
            blocked_result_ids.append(fixture["id"])

    required_ids = set(query["required_document_ids"])
    forbidden_ids = set(query["forbidden_document_ids"])
    first_required_rank = _first_required_rank(eligible_result_ids, required_ids)
    forbidden_violations = sorted(forbidden_ids & set(eligible_result_ids))
    blocked_credit = [
        result_id
        for result_id in eligible_result_ids
        if fixtures_by_id[result_id]["source_health"] in BLOCKED_SOURCE_HEALTH
        or fixtures_by_id[result_id]["approval_state"] != "approved"
    ]

    result = {
        "query_id": query["id"],
        "category": query["category"],
        "query_text": query["query_text"],
        "latency_ms": latency_ms,
        "raw_result_ids": raw_result_ids,
        "eligible_result_ids": eligible_result_ids,
        "blocked_result_ids": blocked_result_ids,
        "overdue_result_ids": overdue_result_ids,
        "required_result_credit": 1 if first_required_rank is not None else 0,
        "required_rank": first_required_rank,
        "recall_at_1_hit": bool(eligible_result_ids[:1] and eligible_result_ids[0] in required_ids),
        "recall_at_3_hit": bool(required_ids & set(eligible_result_ids[:3])),
        "reciprocal_rank": round(1 / first_required_rank, 6)
        if first_required_rank is not None
        else 0.0,
        "forbidden_result_violations": len(forbidden_violations),
        "forbidden_result_ids": forbidden_violations,
        "blocked_source_violations": len(blocked_credit),
    }
    if extra:
        result.update(extra)
    return result


def _summarize_results(query_results: list[dict[str, Any]]) -> dict[str, Any]:
    query_count = len(query_results)
    latency_values = [query["latency_ms"] for query in query_results]
    return {
        "query_count": query_count,
        "categories": sorted({query["category"] for query in query_results}),
        "recall_at_1": _mean(query["recall_at_1_hit"] for query in query_results),
        "recall_at_3": _mean(query["recall_at_3_hit"] for query in query_results),
        "mean_reciprocal_rank": round(
            sum(query["reciprocal_rank"] for query in query_results) / query_count, 6
        ),
        "forbidden_result_violations": sum(
            query["forbidden_result_violations"] for query in query_results
        ),
        "blocked_source_violations": sum(
            query["blocked_source_violations"] for query in query_results
        ),
        "latency_ms": {
            "min": min(latency_values),
            "max": max(latency_values),
            "mean": round(sum(latency_values) / query_count, 3),
        },
    }


def _inspect_embedding_model(client: Any, embedding_model: str) -> dict[str, Any]:
    try:
        payload = client.show_model(embedding_model)
    except FileNotFoundError as exc:
        raise BenchmarkValidationError(
            f"{embedding_model} is not installed. Install the local embedding model and re-run dense indexing."
        ) from exc
    except Exception as exc:
        raise BenchmarkValidationError(
            f"Embedding service could not inspect {embedding_model}. Start the local embedding service and confirm the model is available. Detail: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise BenchmarkValidationError("Embedding model inspection returned a non-object response")
    return _embedding_model_identity(payload, embedding_model)


def _embedding_model_identity(payload: dict[str, Any], embedding_model: str) -> dict[str, Any]:
    if isinstance(payload.get("identity"), dict):
        return _json_roundtrip(payload["identity"])
    identity = {
        "model": payload.get("model") or payload.get("name") or embedding_model,
    }
    for key in ("details", "model_info", "modified_at", "digest"):
        if key in payload:
            identity[key] = payload[key]
    return _json_roundtrip(identity)


def _build_dense_index(
    fixtures: list[dict[str, Any]],
    *,
    corpus_identity: dict[str, Any],
    client: Any,
    embedding_model: str,
    model_identity: dict[str, Any],
    index_path: str | Path | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    operations = _empty_dense_operations()
    vectors: list[dict[str, Any]] = []
    vector_dimensions: int | None = None

    for fixture in fixtures:
        vector, payload = _embed_text(
            client,
            embedding_model,
            fixture["content"],
            f"{fixture['id']} content",
        )
        _record_embedding_payload(operations, payload)
        if vector_dimensions is None:
            vector_dimensions = len(vector)
        elif len(vector) != vector_dimensions:
            raise BenchmarkValidationError(
                f"{fixture['id']} invalid embedding vector: expected {vector_dimensions} dimensions, got {len(vector)}"
            )
        vectors.append({"id": fixture["id"], "vector": vector})

    if vector_dimensions is None:
        raise BenchmarkValidationError("Dense index cannot be built from an empty corpus")

    metadata = {
        "engine": DENSE_INDEX_ENGINE,
        "schema_version": DENSE_INDEX_SCHEMA_VERSION,
        "embedding_provider": "ollama",
        "embedding_model": embedding_model,
        "embedding_model_identity": model_identity,
        "vector_dimensions": vector_dimensions,
        "corpus_fixture_identity": corpus_identity,
        "document_count": len(fixtures),
    }
    index = {"metadata": metadata, "vectors": vectors}
    operations["dense_indexing_wall_time_ms"] = round((time.perf_counter() - started) * 1000, 3)

    if index_path is not None:
        try:
            _write_json_atomic(index, index_path)
        except OSError as exc:
            raise BenchmarkValidationError(
                f"Failed to write dense index at {index_path}. Check the directory and permissions, then re-run indexing. Detail: {exc}"
            ) from exc
        operations["dense_index_size_bytes"] = Path(index_path).stat().st_size

    return index, operations


def _load_compatible_dense_index(
    index_path: str | Path,
    *,
    expected_corpus_identity: dict[str, Any],
    embedding_model: str,
    model_identity: dict[str, Any],
    client: Any,
) -> dict[str, Any]:
    path = Path(index_path)
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DenseIndexCompatibilityError(
            f"Dense index {path} does not exist. Re-index required."
        ) from exc
    except json.JSONDecodeError as exc:
        raise DenseIndexCompatibilityError(
            f"Dense index {path} is not valid JSON. Re-index required."
        ) from exc

    if not isinstance(index, dict) or not isinstance(index.get("metadata"), dict):
        raise DenseIndexCompatibilityError(
            f"Dense index {path} is missing metadata. Re-index required."
        )
    if not isinstance(index.get("vectors"), list):
        raise DenseIndexCompatibilityError(
            f"Dense index {path} is missing vectors. Re-index required."
        )

    metadata = index["metadata"]
    expected_fields = {
        "schema_version": DENSE_INDEX_SCHEMA_VERSION,
        "engine": DENSE_INDEX_ENGINE,
        "embedding_model": embedding_model,
        "embedding_model_identity": model_identity,
        "corpus_fixture_identity": expected_corpus_identity,
    }
    mismatches = [
        key for key, expected in expected_fields.items() if metadata.get(key) != expected
    ]
    if mismatches:
        raise DenseIndexCompatibilityError(
            f"Dense index metadata mismatch for {', '.join(sorted(mismatches))}. Re-index required."
        )

    probe_vector, _payload = _embed_text(
        client,
        embedding_model,
        "dense index compatibility probe",
        "dense index compatibility probe",
    )
    if metadata.get("vector_dimensions") != len(probe_vector):
        raise DenseIndexCompatibilityError(
            "Dense index vector dimension mismatch. Re-index required."
        )

    for item in index["vectors"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise DenseIndexCompatibilityError(
                f"Dense index {path} contains malformed vector records. Re-index required."
            )
        vector = item.get("vector")
        _validate_embedding_vector(vector, f"{item['id']} indexed vector")
        if len(vector) != metadata["vector_dimensions"]:
            raise DenseIndexCompatibilityError(
                f"Dense index vector for {item['id']} has the wrong dimensions. Re-index required."
            )
    return index


def _embed_text(
    client: Any,
    embedding_model: str,
    text: str,
    label: str,
) -> tuple[list[float], dict[str, Any]]:
    try:
        payload = client.embed(embedding_model, text)
    except Exception as exc:
        raise BenchmarkValidationError(
            f"Embedding service failed for {label}. Start the local embedding service, install {embedding_model}, and retry. Detail: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise BenchmarkValidationError(f"{label} embedding response must be a JSON object")
    vector = _extract_embedding_vector(payload)
    return _validate_embedding_vector(vector, label), payload


def _extract_embedding_vector(payload: dict[str, Any]) -> Any:
    if "embedding" in payload:
        return payload["embedding"]
    embeddings = payload.get("embeddings")
    if isinstance(embeddings, list) and embeddings:
        first = embeddings[0]
        if isinstance(first, dict) and "embedding" in first:
            return first["embedding"]
        return first
    return None


def _validate_embedding_vector(value: Any, label: str) -> list[float]:
    if not isinstance(value, list) or not value:
        raise BenchmarkValidationError(f"{label} invalid embedding vector: expected a non-empty list")
    vector: list[float] = []
    for item in value:
        if not isinstance(item, int | float) or isinstance(item, bool) or not math.isfinite(item):
            raise BenchmarkValidationError(
                f"{label} invalid embedding vector: dimensions must be finite numbers"
            )
        vector.append(float(item))
    return vector


def _record_embedding_payload(operations: dict[str, Any], payload: dict[str, Any]) -> None:
    operations["embedding_call_count"] += 1
    for key in ("total_duration", "load_duration", "prompt_eval_duration"):
        duration = _duration_ms(payload.get(key))
        if duration is not None:
            operations["embedding_service_durations_ms"].setdefault(key, []).append(duration)
            if key == "load_duration" and duration > 0:
                operations["cold_embedding_load_observed"] = True


def _duration_ms(value: Any) -> float | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    return round(float(value) / 1_000_000, 3)


def _empty_dense_operations() -> dict[str, Any]:
    return {
        "dense_indexing_wall_time_ms": 0.0,
        "dense_index_size_bytes": 0,
        "embedding_call_count": 0,
        "embedding_service_durations_ms": {},
        "cold_embedding_load_observed": False,
    }


def _finalize_dense_operations(operations: dict[str, Any]) -> dict[str, Any]:
    finalized = dict(operations)
    finalized["process_peak_resident_memory_mb"] = _process_peak_resident_memory_mb()
    return finalized


def _process_peak_resident_memory_mb() -> float | None:
    try:
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:
        return None
    if sys.platform == "darwin":
        return round(value / (1024 * 1024), 3)
    return round(value / 1024, 3)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise BenchmarkValidationError(
            f"Dense query vector has {len(left)} dimensions but index vector has {len(right)}"
        )
    dot_product = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot_product / (left_norm * right_norm)


def _fixture_identity(
    corpus_path: str | Path,
    queries_path: str | Path,
    fixtures: list[dict[str, Any]],
    queries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "corpus_path": str(corpus_path),
        "corpus_sha256": _sha256_file(corpus_path),
        "queries_path": str(queries_path),
        "queries_sha256": _sha256_file(queries_path),
        "document_ids": [fixture["id"] for fixture in fixtures],
        "query_ids": [query["id"] for query in queries],
    }


def _corpus_fixture_identity(
    corpus_path: str | Path, fixtures: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "corpus_path": str(corpus_path),
        "corpus_sha256": _sha256_file(corpus_path),
        "document_ids": [fixture["id"] for fixture in fixtures],
    }


def _json_roundtrip(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True))


def _is_eligible_for_query(fixture: dict[str, Any], query: dict[str, Any]) -> bool:
    return (
        fixture["approval_state"] == "approved"
        and fixture["source_health"] in query["allowed_source_health"]
        and fixture["source_health"] not in BLOCKED_SOURCE_HEALTH
        and _matches_metadata_filters(fixture, query["metadata_filters"])
    )


def _is_blocked_source(fixture: dict[str, Any]) -> bool:
    return (
        fixture["approval_state"] != "approved"
        or fixture["source_health"] in BLOCKED_SOURCE_HEALTH
    )


def _matches_metadata_filters(
    fixture: dict[str, Any], filters: dict[str, Any]
) -> bool:
    for key, expected in filters.items():
        actual = fixture.get(key)
        if key == "topic_tags":
            actual_tags = set(actual if isinstance(actual, list) else [])
            expected_tags = set(expected if isinstance(expected, list) else [expected])
            if not expected_tags <= actual_tags:
                return False
        elif isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _first_required_rank(
    eligible_result_ids: list[str], required_ids: set[str]
) -> int | None:
    for index, result_id in enumerate(eligible_result_ids, start=1):
        if result_id in required_ids:
            return index
    return None


def _validate_fixtures(fixtures: list[dict[str, Any]]) -> None:
    if not fixtures:
        raise BenchmarkValidationError("corpus fixtures must not be empty")

    required = {
        "id",
        "publisher",
        "official_url",
        "topic_tags",
        "language",
        "approval_state",
        "source_health",
        "checked_at_utc",
        "content_origin",
        "content",
    }
    seen_ids: set[str] = set()
    for index, fixture in enumerate(fixtures):
        label = f"corpus fixture {index}"
        _require_keys(fixture, required, label)
        fixture_id = fixture["id"]
        if not isinstance(fixture_id, str) or not FIXTURE_ID_PATTERN.fullmatch(fixture_id):
            raise BenchmarkValidationError(f"{label} has invalid stable id {fixture_id!r}")
        if fixture_id in seen_ids:
            raise BenchmarkValidationError(f"duplicate corpus fixture id {fixture_id!r}")
        seen_ids.add(fixture_id)
        if fixture["content_origin"] != "project-authored-fixture":
            raise BenchmarkValidationError(
                f"{fixture_id} content_origin must be project-authored-fixture"
            )
        if "conversation" in str(fixture["content"]).casefold():
            raise BenchmarkValidationError(f"{fixture_id} content must not contain conversation data")
        if fixture["language"] != "da":
            raise BenchmarkValidationError(f"{fixture_id} language must be da")
        if fixture["approval_state"] not in APPROVAL_STATES:
            raise BenchmarkValidationError(
                f"{fixture_id} approval_state must be one of {sorted(APPROVAL_STATES)}"
            )
        if fixture["source_health"] not in SOURCE_HEALTH_STATES:
            raise BenchmarkValidationError(
                f"{fixture_id} source_health must be one of {sorted(SOURCE_HEALTH_STATES)}"
            )
        if not isinstance(fixture["topic_tags"], list) or not fixture["topic_tags"]:
            raise BenchmarkValidationError(f"{fixture_id} topic_tags must be a non-empty list")
        if not str(fixture["official_url"]).startswith("https://"):
            raise BenchmarkValidationError(f"{fixture_id} official_url must be HTTPS")
        _parse_utc_timestamp(fixture["checked_at_utc"], f"{fixture_id} checked_at_utc")


def _validate_queries(queries: list[dict[str, Any]], fixture_ids: set[str]) -> None:
    if not queries:
        raise BenchmarkValidationError("evaluation queries must not be empty")

    required = {
        "id",
        "query_text",
        "category",
        "required_document_ids",
        "forbidden_document_ids",
        "allowed_source_health",
        "metadata_filters",
    }
    seen_ids: set[str] = set()
    for index, query in enumerate(queries):
        label = f"evaluation query {index}"
        _require_keys(query, required, label)
        query_id = query["id"]
        if not isinstance(query_id, str) or not QUERY_ID_PATTERN.fullmatch(query_id):
            raise BenchmarkValidationError(f"{label} has invalid stable id {query_id!r}")
        if query_id in seen_ids:
            raise BenchmarkValidationError(f"duplicate evaluation query id {query_id!r}")
        seen_ids.add(query_id)
        if not str(query["query_text"]).strip():
            raise BenchmarkValidationError(f"{query_id} query_text must be non-empty")
        for field in ("required_document_ids", "forbidden_document_ids", "allowed_source_health"):
            if not isinstance(query[field], list):
                raise BenchmarkValidationError(f"{query_id} {field} must be a list")
        if not query["required_document_ids"]:
            raise BenchmarkValidationError(f"{query_id} required_document_ids must be non-empty")
        if not query["allowed_source_health"]:
            raise BenchmarkValidationError(f"{query_id} allowed_source_health must be non-empty")
        unknown_health = set(query["allowed_source_health"]) - SOURCE_HEALTH_STATES
        if unknown_health:
            raise BenchmarkValidationError(
                f"{query_id} references unknown allowed_source_health: {sorted(unknown_health)}"
            )
        unknown_required = set(query["required_document_ids"]) - fixture_ids
        unknown_forbidden = set(query["forbidden_document_ids"]) - fixture_ids
        if unknown_required:
            raise BenchmarkValidationError(
                f"{query_id} references unknown required_document_ids: {sorted(unknown_required)}"
            )
        if unknown_forbidden:
            raise BenchmarkValidationError(
                f"{query_id} references unknown forbidden_document_ids: {sorted(unknown_forbidden)}"
            )
        if not isinstance(query["metadata_filters"], dict):
            raise BenchmarkValidationError(f"{query_id} metadata_filters must be an object")


def _load_json_list(path: Path, label: str) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as json_file:
        value = json.load(json_file)
    if not isinstance(value, list):
        raise BenchmarkValidationError(f"{label} must be a JSON list")
    if not all(isinstance(item, dict) for item in value):
        raise BenchmarkValidationError(f"{label} must contain only JSON objects")
    return value


def _require_keys(value: dict[str, Any], required: set[str], label: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise BenchmarkValidationError(f"{label} is missing required field(s): {', '.join(missing)}")


def _parse_utc_timestamp(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise BenchmarkValidationError(f"{label} must be a UTC timestamp string")
    timestamp = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise BenchmarkValidationError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise BenchmarkValidationError(f"{label} must include UTC timezone")


def _fts_match_expression(query_text: str) -> str:
    tokens = TOKEN_PATTERN.findall(query_text)
    if not tokens:
        raise BenchmarkValidationError("query_text did not contain searchable tokens")
    return " OR ".join(f'"{token.lower()}"' for token in tokens)


def _mean(values: Any) -> float:
    items = list(values)
    return round(sum(1 for item in items if item) / len(items), 6)


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_json_atomic(value: dict[str, Any], path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)


if __name__ == "__main__":
    raise SystemExit(main())
