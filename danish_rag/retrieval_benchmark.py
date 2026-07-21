"""Local lexical, dense, and hybrid benchmarks for approved-source fixtures."""

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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .runtime_policy import load_runtime_policy


BENCHMARK_ID = "mvp-retrieval-benchmark-issue-27"
DENSE_BENCHMARK_ID = "mvp-dense-retrieval-benchmark-issue-28"
HYBRID_BENCHMARK_ID = "mvp-hybrid-retrieval-comparison-issue-29"
DENSE_INDEX_SCHEMA_VERSION = "dense-retrieval-index-v1"
DENSE_INDEX_ENGINE = "local-dense-json"
DEFAULT_CORPUS_PATH = "data/retrieval_benchmark/corpus-fixtures.json"
DEFAULT_LEXICAL_QUERIES_PATH = "data/retrieval_benchmark/evaluation-queries.json"
DEFAULT_DENSE_QUERIES_PATH = "data/retrieval_benchmark/dense-evaluation-queries.json"
DEFAULT_LEXICAL_OUTPUT_PATH = "docs/progress/issue-27-retrieval-benchmark.json"
DEFAULT_DENSE_OUTPUT_PATH = "docs/progress/issue-28-dense-retrieval-benchmark.json"
DEFAULT_HYBRID_OUTPUT_PATH = "docs/progress/issue-29-hybrid-retrieval-comparison.json"
DEFAULT_HYBRID_RECOMMENDATION_PATH = (
    "docs/progress/issue-29-hybrid-retrieval-recommendation.md"
)
DEFAULT_DENSE_INDEX_PATH = "docs/progress/issue-28-dense-index.json"
DEFAULT_RUNTIME_PROBE_PATH = "docs/progress/issue-26-runtime-probe.json"
DEFAULT_RRF_K = 60
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


@dataclass(frozen=True)
class DenseIndexCompatibilityIdentity:
    schema_version: str
    engine: str
    embedding_model: str
    embedding_model_identity: dict[str, Any]
    corpus_fixture_identity: dict[str, Any]

    @classmethod
    def for_run(
        cls,
        *,
        embedding_model: str,
        embedding_model_identity: dict[str, Any],
        corpus_fixture_identity: dict[str, Any],
    ) -> "DenseIndexCompatibilityIdentity":
        return cls(
            schema_version=DENSE_INDEX_SCHEMA_VERSION,
            engine=DENSE_INDEX_ENGINE,
            embedding_model=embedding_model,
            embedding_model_identity=_json_roundtrip(embedding_model_identity),
            corpus_fixture_identity=_json_roundtrip(corpus_fixture_identity),
        )

    def metadata_fields(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "engine": self.engine,
            "embedding_model": self.embedding_model,
            "embedding_model_identity": self.embedding_model_identity,
            "corpus_fixture_identity": self.corpus_fixture_identity,
        }

    def mismatched_metadata_fields(self, metadata: dict[str, Any]) -> list[str]:
        return [
            key
            for key, expected in self.metadata_fields().items()
            if metadata.get(key) != expected
        ]


class OllamaEmbeddingClient:
    def __init__(self, endpoint: str, timeout_seconds: float = 60.0):
        self.endpoint = endpoint.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def get_version(self) -> dict[str, Any]:
        return self._request("GET", "/api/version")

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
        "category_metrics": _category_metrics(query_results),
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
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    fixtures, queries = load_benchmark_inputs(corpus_path, queries_path)
    fixture_identity = _fixture_identity(corpus_path, queries_path, fixtures, queries)
    corpus_identity = _corpus_fixture_identity(corpus_path, fixtures)

    policy: dict[str, Any] | None = None
    if client is None or embedding_model is None:
        policy = load_runtime_policy(policy_path)
    if embedding_model is None:
        embedding_model = policy["models"]["embedding"]["initial_supported"]  # type: ignore[index]
    endpoint = embedding_endpoint
    if client is None:
        endpoint = embedding_endpoint or policy["providers"]["initial"]["default_endpoint"]  # type: ignore[index]
        client = OllamaEmbeddingClient(
            endpoint,
            timeout_seconds=timeout_seconds if timeout_seconds is not None else 60.0,
        )
    else:
        endpoint = embedding_endpoint or getattr(client, "endpoint", None)

    model_identity = _inspect_embedding_model(client, embedding_model)
    runtime_version_payload = _inspect_embedding_runtime(client)
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
        "configuration": _dense_benchmark_configuration(
            corpus_path=corpus_path,
            queries_path=queries_path,
            policy_path=policy_path,
            embedding_endpoint=endpoint,
            embedding_model=embedding_model,
            index_path=index_path,
            output_path=output_path,
            rebuild_index=rebuild_index,
            timeout_seconds=timeout_seconds,
        ),
        "runtime": _dense_runtime_metadata(
            embedding_endpoint=endpoint,
            embedding_model=embedding_model,
            embedding_model_identity=model_identity,
            version_payload=runtime_version_payload,
        ),
        "fixture_identity": fixture_identity,
        "index": index["metadata"],
        "operations": _finalize_dense_operations(operations),
        "summary": _summarize_results(query_results),
        "queries": query_results,
    }
    if output_path is not None:
        write_benchmark_result(result, output_path)
    return result


def run_hybrid_retrieval_comparison(
    corpus_path: str | Path,
    lexical_queries_path: str | Path,
    dense_queries_path: str | Path,
    *,
    index_path: str | Path | None = None,
    output_path: str | Path | None = None,
    recommendation_path: str | Path | None = None,
    policy_path: str | Path = "config/runtime-policy.json",
    runtime_probe_path: str | Path = DEFAULT_RUNTIME_PROBE_PATH,
    embedding_endpoint: str | None = None,
    embedding_model: str | None = None,
    client: Any | None = None,
    rebuild_index: bool = True,
    rrf_k: int = DEFAULT_RRF_K,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    fixtures, lexical_queries = load_benchmark_inputs(corpus_path, lexical_queries_path)
    _dense_fixtures, dense_queries = load_benchmark_inputs(corpus_path, dense_queries_path)
    queries = _combine_query_sets(lexical_queries, dense_queries)
    fixture_identity = _comparison_fixture_identity(
        corpus_path,
        lexical_queries_path,
        dense_queries_path,
        fixtures,
        lexical_queries,
        dense_queries,
    )
    corpus_identity = _corpus_fixture_identity(corpus_path, fixtures)

    policy = load_runtime_policy(policy_path)
    if embedding_model is None:
        embedding_model = policy["models"]["embedding"]["initial_supported"]
    if client is None:
        endpoint = embedding_endpoint or policy["providers"]["initial"]["default_endpoint"]
        client = OllamaEmbeddingClient(
            endpoint,
            timeout_seconds=timeout_seconds if timeout_seconds is not None else 60.0,
        )
    else:
        endpoint = embedding_endpoint or getattr(client, "endpoint", None)

    model_identity = _inspect_embedding_model(client, embedding_model)
    operations_started = time.perf_counter()
    fts_started = time.perf_counter()
    connection = build_fts_index(fixtures)
    lexical_indexing_wall_time_ms = round((time.perf_counter() - fts_started) * 1000, 3)

    if rebuild_index:
        index, dense_operations = _build_dense_index(
            fixtures,
            corpus_identity=corpus_identity,
            client=client,
            embedding_model=embedding_model,
            model_identity=model_identity,
            index_path=index_path,
        )
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
        dense_operations = _empty_dense_operations()
        dense_operations["dense_index_size_bytes"] = Path(index_path).stat().st_size

    fixtures_by_id = {fixture["id"]: fixture for fixture in fixtures}
    vectors_by_id = {
        item["id"]: item["vector"] for item in index["vectors"] if isinstance(item, dict)
    }
    lexical_results: list[dict[str, Any]] = []
    dense_results: list[dict[str, Any]] = []
    hybrid_results: list[dict[str, Any]] = []

    try:
        for query in queries:
            lexical_result = _evaluate_query(connection, fixtures_by_id, query)
            dense_result = _evaluate_dense_query(
                fixtures_by_id,
                vectors_by_id,
                query,
                client=client,
                embedding_model=embedding_model,
                operations=dense_operations,
            )
            hybrid_result = _evaluate_hybrid_query(
                fixtures_by_id,
                query,
                lexical_result["raw_result_ids"],
                dense_result["raw_result_ids"],
                rrf_k=rrf_k,
            )
            lexical_results.append(lexical_result)
            dense_results.append(dense_result)
            hybrid_results.append(hybrid_result)
    finally:
        connection.close()

    operations = {
        "comparison_wall_time_ms": round((time.perf_counter() - operations_started) * 1000, 3),
        "lexical_indexing_wall_time_ms": lexical_indexing_wall_time_ms,
        "dense": _finalize_dense_operations(dense_operations),
    }
    candidates = {
        "lexical": _candidate_report("lexical", lexical_results),
        "dense": _candidate_report("dense", dense_results),
        "hybrid": _candidate_report(
            "hybrid",
            hybrid_results,
            fusion={"algorithm": "rrf", "k": rrf_k, "sources": ["lexical", "dense"]},
        ),
    }
    configuration = _comparison_configuration(
        policy,
        corpus_path=corpus_path,
        lexical_queries_path=lexical_queries_path,
        dense_queries_path=dense_queries_path,
        policy_path=policy_path,
        runtime_probe_path=runtime_probe_path,
        embedding_endpoint=endpoint,
        embedding_model=embedding_model,
        index_path=index_path,
        output_path=output_path,
        recommendation_path=recommendation_path,
        rebuild_index=rebuild_index,
        rrf_k=rrf_k,
        timeout_seconds=timeout_seconds,
    )
    recommendation = _recommend_candidate(candidates, index, operations, configuration)
    human_recommendation = _human_recommendation_summary(recommendation, candidates)
    result = {
        "benchmark_id": HYBRID_BENCHMARK_ID,
        "executed_at_utc": started.isoformat(),
        "fixture_identity": fixture_identity,
        "configuration": configuration,
        "index": index["metadata"],
        "operations": operations,
        "candidates": candidates,
        "recommendation": recommendation,
        "human_recommendation": human_recommendation,
        "summary": {
            "selected_candidate": recommendation["selected_candidate"],
            "query_count": len(queries),
            "candidate_count": len(candidates),
        },
    }
    if output_path is not None:
        write_benchmark_result(result, output_path)
    if recommendation_path is not None:
        write_hybrid_recommendation(result, recommendation_path)
    return result


def write_benchmark_result(result: dict[str, Any], path: str | Path) -> None:
    _write_json_atomic(result, path)


def write_hybrid_recommendation(result: dict[str, Any], path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.write_text(_format_hybrid_recommendation(result), encoding="utf-8")
    temporary_path.replace(output_path)


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
        choices=("lexical", "dense", "compare"),
        default="lexical",
        help=(
            "Run issue #27 lexical, issue #28 dense, or issue #29 comparison "
            "benchmark."
        ),
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
        "--dense-queries",
        default=DEFAULT_DENSE_QUERIES_PATH,
        help="Path to the dense/paraphrase query JSON used by --mode compare.",
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
        "--recommendation-output",
        default=DEFAULT_HYBRID_RECOMMENDATION_PATH,
        help="Path where --mode compare writes the human-readable recommendation.",
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
        help="Embedding model. Defaults to the runtime policy initial supported model.",
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
    parser.add_argument(
        "--runtime-probe",
        default=DEFAULT_RUNTIME_PROBE_PATH,
        help="Runtime probe evidence used by --mode compare for compatibility metadata.",
    )
    parser.add_argument(
        "--rrf-k",
        default=DEFAULT_RRF_K,
        type=int,
        help="Reciprocal-rank fusion k value used by --mode compare.",
    )
    args = parser.parse_args(argv)

    queries_path = args.queries or (
        DEFAULT_DENSE_QUERIES_PATH
        if args.mode == "dense"
        else DEFAULT_LEXICAL_QUERIES_PATH
    )
    output_path = args.output or (
        DEFAULT_DENSE_OUTPUT_PATH
        if args.mode == "dense"
        else DEFAULT_HYBRID_OUTPUT_PATH
        if args.mode == "compare"
        else DEFAULT_LEXICAL_OUTPUT_PATH
    )
    failure_output = args.failure_output or output_path.replace(".json", ".failed.json")
    benchmark_id = (
        DENSE_BENCHMARK_ID
        if args.mode == "dense"
        else HYBRID_BENCHMARK_ID
        if args.mode == "compare"
        else BENCHMARK_ID
    )

    try:
        if args.mode in {"dense", "compare"}:
            policy = load_runtime_policy(args.policy)
            endpoint = args.embedding_endpoint or policy["providers"]["initial"]["default_endpoint"]
            client = OllamaEmbeddingClient(endpoint, timeout_seconds=args.timeout)
        if args.mode == "compare":
            result = run_hybrid_retrieval_comparison(
                args.corpus,
                queries_path,
                args.dense_queries,
                index_path=args.index,
                output_path=output_path,
                recommendation_path=args.recommendation_output,
                policy_path=args.policy,
                runtime_probe_path=args.runtime_probe,
                embedding_endpoint=endpoint,
                embedding_model=args.embedding_model,
                client=client,
                rebuild_index=not args.reuse_index,
                rrf_k=args.rrf_k,
                timeout_seconds=args.timeout,
            )
        elif args.mode == "dense":
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
                timeout_seconds=args.timeout,
            )
        else:
            result = run_retrieval_benchmark(args.corpus, queries_path, output_path=output_path)
    except BenchmarkValidationError as exc:
        write_benchmark_failure(exc, failure_output, benchmark_id=benchmark_id)
        print(f"Benchmark validation failed: {exc}", file=sys.stderr)
        print(f"Failure evidence: {failure_output}", file=sys.stderr)
        return 1

    if args.mode == "compare":
        print(
            "Hybrid retrieval comparison complete: "
            f"selected={result['recommendation']['selected_candidate']}, "
            f"hybrid Recall@3={result['candidates']['hybrid']['summary']['recall_at_3']:.3f}"
        )
        print(f"Evidence: {output_path}")
        print(f"Recommendation: {args.recommendation_output}")
        return 0

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
    embedding_load_duration_ms = _duration_ms(payload.get("load_duration"))
    warm_retrieval_latency_ms = _warm_retrieval_latency_ms(
        embedding_latency_ms=embedding_latency_ms,
        similarity_latency_ms=similarity_latency_ms,
        embedding_load_duration_ms=embedding_load_duration_ms,
    )

    return _evaluate_ranked_result_ids(
        fixtures_by_id,
        query,
        raw_result_ids,
        latency_ms=latency_ms,
        extra={
            "embedding_latency_ms": embedding_latency_ms,
            "warm_similarity_latency_ms": similarity_latency_ms,
            "embedding_load_duration_ms": embedding_load_duration_ms,
            "warm_retrieval_latency_ms": warm_retrieval_latency_ms,
        },
    )


def _evaluate_hybrid_query(
    fixtures_by_id: dict[str, dict[str, Any]],
    query: dict[str, Any],
    lexical_result_ids: list[str],
    dense_result_ids: list[str],
    *,
    rrf_k: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    raw_result_ids, fusion_scores = _reciprocal_rank_fusion(
        [lexical_result_ids, dense_result_ids],
        k=rrf_k,
    )
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    return _evaluate_ranked_result_ids(
        fixtures_by_id,
        query,
        raw_result_ids[:10],
        latency_ms=latency_ms,
        extra={
            "fusion_scores": fusion_scores,
            "fusion_sources": {
                "lexical": lexical_result_ids,
                "dense": dense_result_ids,
            },
        },
    )


def _reciprocal_rank_fusion(
    rankings: list[list[str]], *, k: int = DEFAULT_RRF_K
) -> tuple[list[str], dict[str, float]]:
    if k < 0:
        raise BenchmarkValidationError("RRF k must be non-negative")
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    first_source: dict[str, int] = {}
    for source_index, ranking in enumerate(rankings):
        seen_in_source: set[str] = set()
        for rank, result_id in enumerate(ranking, start=1):
            if result_id in seen_in_source:
                continue
            seen_in_source.add(result_id)
            scores[result_id] = scores.get(result_id, 0.0) + (1.0 / (k + rank))
            best_rank[result_id] = min(best_rank.get(result_id, rank), rank)
            first_source.setdefault(result_id, source_index)
    ranked = sorted(
        scores,
        key=lambda result_id: (
            -scores[result_id],
            best_rank[result_id],
            first_source[result_id],
            result_id,
        ),
    )
    return ranked, {result_id: round(scores[result_id], 9) for result_id in ranked}


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

    required_evidence_ids = [
        result_id
        for result_id in query["required_document_ids"]
        if _is_eligible_for_query(fixtures_by_id[result_id], query)
    ]
    required_evidence_set = set(required_evidence_ids)
    forbidden_ids = set(query["forbidden_document_ids"])
    first_required_rank = _first_required_rank(
        eligible_result_ids, required_evidence_set
    )
    required_evidence_query = bool(required_evidence_ids)
    recall_at_1_hit = required_evidence_query and required_evidence_set <= set(
        eligible_result_ids[:1]
    )
    recall_at_3_hit = required_evidence_query and required_evidence_set <= set(
        eligible_result_ids[:3]
    )
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
        "required_evidence_result_ids": required_evidence_ids,
        "required_evidence_query": required_evidence_query,
        "blocked_result_ids": blocked_result_ids,
        "overdue_result_ids": overdue_result_ids,
        "required_result_credit": 1 if recall_at_3_hit else 0,
        "required_rank": first_required_rank,
        "recall_at_1_hit": recall_at_1_hit,
        "recall_at_3_hit": recall_at_3_hit,
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
    required_evidence_results = [
        query for query in query_results if query.get("required_evidence_query")
    ]
    required_evidence_query_count = len(required_evidence_results)
    latency_values = [query["latency_ms"] for query in query_results]
    summary = {
        "query_count": query_count,
        "required_evidence_query_count": required_evidence_query_count,
        "categories": sorted({query["category"] for query in query_results}),
        "recall_at_1": _mean(
            query["recall_at_1_hit"] for query in required_evidence_results
        )
        if required_evidence_results
        else 0.0,
        "recall_at_3": _mean(
            query["recall_at_3_hit"] for query in required_evidence_results
        )
        if required_evidence_results
        else 0.0,
        "mean_reciprocal_rank": round(
            sum(query["reciprocal_rank"] for query in required_evidence_results)
            / required_evidence_query_count,
            6,
        )
        if required_evidence_results
        else 0.0,
        "forbidden_result_violations": sum(
            query["forbidden_result_violations"] for query in query_results
        ),
        "blocked_source_violations": sum(
            query["blocked_source_violations"] for query in query_results
        ),
        "latency_ms": _numeric_summary(latency_values),
    }
    for key in ("warm_retrieval_latency_ms", "embedding_load_duration_ms"):
        values = [
            query[key]
            for query in query_results
            if isinstance(query.get(key), int | float)
            and not isinstance(query.get(key), bool)
        ]
        if values:
            summary[key] = _numeric_summary(values)
    return summary


def _candidate_report(
    name: str,
    query_results: list[dict[str, Any]],
    *,
    fusion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = {
        "name": name,
        "summary": _summarize_results(query_results),
        "category_metrics": _category_metrics(query_results),
        "queries": query_results,
    }
    if fusion is not None:
        report["fusion"] = fusion
    return report


def _category_metrics(query_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for category in sorted({query["category"] for query in query_results}):
        category_results = [
            query for query in query_results if query["category"] == category
        ]
        metrics[category] = _summarize_results(category_results)
    return metrics


def _recommend_candidate(
    candidates: dict[str, dict[str, Any]],
    index: dict[str, Any],
    operations: dict[str, Any],
    configuration: dict[str, Any],
) -> dict[str, Any]:
    hybrid_rejection_reasons = _hybrid_rejection_reasons(
        candidates,
        index,
        operations,
        configuration,
    )
    rejected_candidates: dict[str, list[str]] = {}
    if hybrid_rejection_reasons:
        rejected_candidates["hybrid"] = hybrid_rejection_reasons
        selected = _best_alternative_candidate(candidates, excluded={"hybrid"})
    else:
        selected = "hybrid"

    return {
        "selected_candidate": selected,
        "recommended_for": "later human architecture approval gate",
        "production_thresholds": "out_of_scope",
        "rejected_candidates": rejected_candidates,
        "selection_rule": {
            "hybrid_requires_no_blocked_source_violations": True,
            "hybrid_must_not_regress_exact_term_recall_at_3_against_lexical": True,
            "hybrid_must_match_or_improve_english_and_typo_recall_at_3": True,
            "hybrid_requires_complete_compatibility_metadata": True,
            "hybrid_must_operate_within_runtime_baseline": True,
        },
        "candidate_metrics": {
            name: {
                "summary": candidate["summary"],
                "category_metrics": candidate["category_metrics"],
            }
            for name, candidate in candidates.items()
        },
    }


def _hybrid_rejection_reasons(
    candidates: dict[str, dict[str, Any]],
    index: dict[str, Any],
    operations: dict[str, Any],
    configuration: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    hybrid = candidates["hybrid"]
    lexical = candidates["lexical"]
    dense = candidates["dense"]

    if hybrid["summary"]["blocked_source_violations"] != 0:
        reasons.append("hybrid returned credit for blocked or unapproved sources")

    rrf_k = (
        configuration.get("retrieval", {})
        .get("hybrid_fusion", {})
        .get("k")
    )
    if rrf_k != DEFAULT_RRF_K:
        reasons.append("hybrid RRF k differs from the reviewed default configuration")

    exact_category = "exact-danish-terminology"
    hybrid_exact = _recall_at_3_for_category(hybrid, exact_category)
    lexical_exact = _recall_at_3_for_category(lexical, exact_category)
    if hybrid_exact < lexical_exact:
        reasons.append(
            "hybrid regressed exact Danish terminology Recall@3 against lexical retrieval"
        )

    for category in ("english-paraphrase", "realistic-typo"):
        hybrid_recall = _recall_at_3_for_category(hybrid, category)
        required_recall = max(
            _recall_at_3_for_category(lexical, category),
            _recall_at_3_for_category(dense, category),
        )
        if hybrid_recall < required_recall:
            reasons.append(
                f"hybrid regressed {category} Recall@3 against a single-mode candidate"
            )

    metadata = index.get("metadata", {})
    required_index_fields = {
        "engine",
        "schema_version",
        "embedding_model",
        "embedding_model_identity",
        "vector_dimensions",
        "corpus_fixture_identity",
    }
    missing_index_fields = sorted(required_index_fields - set(metadata))
    if missing_index_fields:
        reasons.append(
            "hybrid compatibility metadata is incomplete: "
            + ", ".join(missing_index_fields)
        )

    runtime = configuration.get("runtime_baseline", {})
    memory_limit_mb = runtime.get("recommended_system_ram_mb")
    peak_memory_mb = operations["dense"].get("process_peak_resident_memory_mb")
    if (
        isinstance(memory_limit_mb, int | float)
        and isinstance(peak_memory_mb, int | float)
        and peak_memory_mb > memory_limit_mb
    ):
        reasons.append("hybrid exceeded the recorded runtime memory baseline")
    if not runtime.get("baseline_id"):
        reasons.append("runtime baseline metadata is missing")

    return reasons


def _recall_at_3_for_category(candidate: dict[str, Any], category: str) -> float:
    metrics = candidate["category_metrics"].get(category)
    if metrics is None:
        return 0.0
    return metrics["recall_at_3"]


def _best_alternative_candidate(
    candidates: dict[str, dict[str, Any]], *, excluded: set[str]
) -> str:
    eligible = [
        (name, candidate)
        for name, candidate in candidates.items()
        if name not in excluded
    ]
    ranked = sorted(
        eligible,
        key=lambda item: (
            item[1]["summary"]["blocked_source_violations"],
            -item[1]["summary"]["recall_at_3"],
            -item[1]["summary"]["mean_reciprocal_rank"],
            item[1]["summary"]["latency_ms"]["mean"],
            item[0],
        ),
    )
    return ranked[0][0]


def _human_recommendation_summary(
    recommendation: dict[str, Any], candidates: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    return {
        "selected_candidate": recommendation["selected_candidate"],
        "production_thresholds": recommendation["production_thresholds"],
        "candidate_metrics": {
            name: candidate["summary"] for name, candidate in candidates.items()
        },
        "rejected_candidates": recommendation["rejected_candidates"],
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


def _inspect_embedding_runtime(client: Any) -> dict[str, Any] | None:
    get_version = getattr(client, "get_version", None)
    if not callable(get_version):
        return None
    try:
        payload = get_version()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return _json_roundtrip(payload)


def _dense_runtime_metadata(
    *,
    embedding_endpoint: str | None,
    embedding_model: str,
    embedding_model_identity: dict[str, Any],
    version_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    version = None
    if isinstance(version_payload, dict) and version_payload.get("version") is not None:
        version = str(version_payload["version"])
    return {
        "embedding_provider": "ollama",
        "embedding_endpoint": embedding_endpoint,
        "embedding_model": embedding_model,
        "embedding_model_identity": embedding_model_identity,
        "provider_version": version,
        "provider_version_payload": version_payload,
    }


def _dense_benchmark_configuration(
    *,
    corpus_path: str | Path,
    queries_path: str | Path,
    policy_path: str | Path,
    embedding_endpoint: str | None,
    embedding_model: str,
    index_path: str | Path | None,
    output_path: str | Path | None,
    rebuild_index: bool,
    timeout_seconds: float | None,
) -> dict[str, Any]:
    configuration = {
        "corpus_path": str(corpus_path),
        "queries_path": str(queries_path),
        "policy_path": str(policy_path),
        "embedding_endpoint": embedding_endpoint,
        "embedding_model": embedding_model,
        "index_path": str(index_path) if index_path is not None else None,
        "output_path": str(output_path) if output_path is not None else None,
        "index_mode": "rebuild" if rebuild_index else "reuse",
    }
    if timeout_seconds is not None:
        configuration["timeout_seconds"] = timeout_seconds
    return configuration


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

    compatibility_identity = DenseIndexCompatibilityIdentity.for_run(
        embedding_model=embedding_model,
        embedding_model_identity=model_identity,
        corpus_fixture_identity=corpus_identity,
    )
    metadata = {
        **compatibility_identity.metadata_fields(),
        "embedding_provider": "ollama",
        "vector_dimensions": vector_dimensions,
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
    expected_identity = DenseIndexCompatibilityIdentity.for_run(
        embedding_model=embedding_model,
        embedding_model_identity=model_identity,
        corpus_fixture_identity=expected_corpus_identity,
    )
    mismatches = expected_identity.mismatched_metadata_fields(metadata)
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


def _warm_retrieval_latency_ms(
    *,
    embedding_latency_ms: float,
    similarity_latency_ms: float,
    embedding_load_duration_ms: float | None,
) -> float:
    if embedding_load_duration_ms is None:
        return round(embedding_latency_ms + similarity_latency_ms, 3)
    warm_embedding_latency_ms = max(embedding_latency_ms - embedding_load_duration_ms, 0.0)
    return round(warm_embedding_latency_ms + similarity_latency_ms, 3)


def _numeric_summary(values: list[float]) -> dict[str, float]:
    return {
        "min": min(values),
        "max": max(values),
        "mean": round(sum(values) / len(values), 3),
    }


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


def _comparison_fixture_identity(
    corpus_path: str | Path,
    lexical_queries_path: str | Path,
    dense_queries_path: str | Path,
    fixtures: list[dict[str, Any]],
    lexical_queries: list[dict[str, Any]],
    dense_queries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "corpus_path": str(corpus_path),
        "corpus_sha256": _sha256_file(corpus_path),
        "document_ids": [fixture["id"] for fixture in fixtures],
        "query_sets": {
            "lexical": {
                "queries_path": str(lexical_queries_path),
                "queries_sha256": _sha256_file(lexical_queries_path),
                "query_ids": [query["id"] for query in lexical_queries],
            },
            "dense": {
                "queries_path": str(dense_queries_path),
                "queries_sha256": _sha256_file(dense_queries_path),
                "query_ids": [query["id"] for query in dense_queries],
            },
        },
        "query_ids": [query["id"] for query in lexical_queries + dense_queries],
    }


def _corpus_fixture_identity(
    corpus_path: str | Path, fixtures: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "corpus_path": str(corpus_path),
        "corpus_sha256": _sha256_file(corpus_path),
        "document_ids": [fixture["id"] for fixture in fixtures],
    }


def _combine_query_sets(
    lexical_queries: list[dict[str, Any]], dense_queries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    queries = lexical_queries + dense_queries
    seen: set[str] = set()
    duplicates: list[str] = []
    for query in queries:
        if query["id"] in seen:
            duplicates.append(query["id"])
        seen.add(query["id"])
    if duplicates:
        raise BenchmarkValidationError(
            f"comparison query ids must be unique: {sorted(duplicates)}"
        )
    return queries


def _comparison_configuration(
    policy: dict[str, Any],
    *,
    corpus_path: str | Path,
    lexical_queries_path: str | Path,
    dense_queries_path: str | Path,
    policy_path: str | Path,
    runtime_probe_path: str | Path,
    embedding_endpoint: str | None,
    embedding_model: str,
    index_path: str | Path | None,
    output_path: str | Path | None,
    recommendation_path: str | Path | None,
    rebuild_index: bool,
    rrf_k: int,
    timeout_seconds: float | None,
) -> dict[str, Any]:
    provider = policy["providers"]["initial"]
    hardware = policy["supported_environment"]["hardware"]
    runtime_probe = _load_optional_json_object(runtime_probe_path)
    provider_version = None
    python_version = None
    if runtime_probe is not None:
        provider_version = runtime_probe.get("provider", {}).get("version")
        python_version = runtime_probe.get("environment", {}).get("python_version")
    return {
        "inputs": {
            "corpus_path": str(corpus_path),
            "lexical_queries_path": str(lexical_queries_path),
            "dense_queries_path": str(dense_queries_path),
        },
        "outputs": {
            "machine_readable_result_path": (
                str(output_path) if output_path is not None else None
            ),
            "human_recommendation_path": (
                str(recommendation_path) if recommendation_path is not None else None
            ),
        },
        "embedding": {
            "endpoint": embedding_endpoint,
            "model": embedding_model,
            "timeout_seconds": timeout_seconds,
        },
        "index": {
            "index_path": str(index_path) if index_path is not None else None,
            "index_mode": "rebuild" if rebuild_index else "reuse",
        },
        "runtime_policy_path": str(policy_path),
        "runtime_probe_path": str(runtime_probe_path),
        "runtime_baseline": {
            "baseline_id": policy["baseline_id"],
            "provider": provider["id"],
            "provider_minimum_version": provider["minimum_version"],
            "provider_observed_version": provider_version,
            "python_observed_version": python_version,
            "recommended_system_ram_mb": hardware["recommended_system_ram_gb"] * 1024,
        },
        "retrieval": {
            "candidate_modes": ["lexical", "dense", "hybrid"],
            "hybrid_fusion": {
                "algorithm": "rrf",
                "k": rrf_k,
                "sources": ["lexical", "dense"],
            },
        },
        "recommendation_scope": {
            "feeds_human_architecture_approval_gate": True,
            "production_thresholds_out_of_scope": True,
        },
    }


def _load_optional_json_object(path: str | Path) -> dict[str, Any] | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        return None
    return value


def _format_hybrid_recommendation(result: dict[str, Any]) -> str:
    recommendation = result["recommendation"]
    selected = recommendation["selected_candidate"]
    output_path = result["configuration"]["outputs"]["machine_readable_result_path"]
    lines = [
        "# Issue 29 Hybrid Retrieval Recommendation",
        "",
        f"Machine-readable result: `{output_path}`",
        f"Executed at UTC: `{result['executed_at_utc']}`",
        f"Selected candidate: {selected}",
        "",
        "Production thresholds remain out of scope and must be decided by the later evaluation decision ticket.",
        "",
        "## Candidate Metrics",
        "",
    ]
    for name, candidate in result["candidates"].items():
        summary = candidate["summary"]
        lines.extend(
            [
                f"### {name}",
                "",
                (
                    "- Required-evidence query count: "
                    f"`{summary['required_evidence_query_count']}`"
                ),
                f"- Recall@1: `{summary['recall_at_1']}`",
                f"- Recall@3: `{summary['recall_at_3']}`",
                f"- Mean reciprocal rank: `{summary['mean_reciprocal_rank']}`",
                f"- Blocked-source violations: `{summary['blocked_source_violations']}`",
                f"- Forbidden-result violations: `{summary['forbidden_result_violations']}`",
                f"- Mean latency ms: `{summary['latency_ms']['mean']}`",
                "",
            ]
        )

    lines.extend(["## Non-Selected Alternatives", ""])
    rejected = recommendation["rejected_candidates"]
    if not rejected:
        lines.append("- No candidate was rejected by the issue 29 rule.")
    else:
        for name, reasons in rejected.items():
            lines.append(f"- {name}: " + "; ".join(reasons))
    lines.extend(
        [
            "",
            "## Compatibility Metadata",
            "",
            f"- Runtime baseline: `{result['configuration']['runtime_baseline']['baseline_id']}`",
            f"- Embedding model: `{result['index']['embedding_model']}`",
            f"- Vector dimensions: `{result['index']['vector_dimensions']}`",
            f"- Corpus SHA-256: `{result['fixture_identity']['corpus_sha256']}`",
            f"- RRF k: `{result['configuration']['retrieval']['hybrid_fusion']['k']}`",
            "",
            "## Recommendation",
            "",
            (
                f"Use `{selected}` as the evidenced retrieval candidate to bring into "
                "the later human architecture approval gate."
            ),
            "",
        ]
    )
    return "\n".join(lines)


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
