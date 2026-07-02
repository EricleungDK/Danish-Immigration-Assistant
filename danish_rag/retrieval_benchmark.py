"""SQLite FTS5 retrieval benchmark for issue #27 approved-source fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BENCHMARK_ID = "mvp-retrieval-benchmark-issue-27"
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


def write_benchmark_result(result: dict[str, Any], path: str | Path) -> None:
    _write_json_atomic(result, path)


def write_benchmark_failure(error: Exception, path: str | Path) -> None:
    failure = {
        "benchmark_id": BENCHMARK_ID,
        "exit_status": 1,
        "diagnostic": str(error),
        "failed_at_utc": datetime.now(UTC).isoformat(),
    }
    _write_json_atomic(failure, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        default="data/retrieval_benchmark/corpus-fixtures.json",
        help="Path to the project-authored corpus fixture JSON.",
    )
    parser.add_argument(
        "--queries",
        default="data/retrieval_benchmark/evaluation-queries.json",
        help="Path to the reviewed evaluation-query JSON.",
    )
    parser.add_argument(
        "--output",
        default="docs/progress/issue-27-retrieval-benchmark.json",
        help="Path where complete benchmark results are written atomically.",
    )
    parser.add_argument(
        "--failure-output",
        default="docs/progress/issue-27-retrieval-benchmark.failed.json",
        help="Path where failed benchmark diagnostics are written atomically.",
    )
    args = parser.parse_args(argv)

    try:
        result = run_retrieval_benchmark(args.corpus, args.queries, output_path=args.output)
    except BenchmarkValidationError as exc:
        write_benchmark_failure(exc, args.failure_output)
        print(f"Benchmark validation failed: {exc}", file=sys.stderr)
        print(f"Failure evidence: {args.failure_output}", file=sys.stderr)
        return 1

    print(
        "Retrieval benchmark complete: "
        f"Recall@1={result['summary']['recall_at_1']:.3f}, "
        f"Recall@3={result['summary']['recall_at_3']:.3f}, "
        f"MRR={result['summary']['mean_reciprocal_rank']:.3f}"
    )
    print(f"Evidence: {args.output}")
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
    eligible_result_ids: list[str] = []
    blocked_result_ids: list[str] = []
    overdue_result_ids: list[str] = []
    for row in rows:
        fixture = fixtures_by_id[row["id"]]
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

    return {
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


def _summarize_results(query_results: list[dict[str, Any]]) -> dict[str, Any]:
    query_count = len(query_results)
    latency_values = [query["latency_ms"] for query in query_results]
    return {
        "query_count": query_count,
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
