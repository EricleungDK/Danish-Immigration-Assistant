import json
import tempfile
import unittest
from pathlib import Path

from danish_rag.retrieval_benchmark import (
    BenchmarkValidationError,
    build_fts_index,
    load_benchmark_inputs,
    run_retrieval_benchmark,
    write_benchmark_failure,
    write_benchmark_result,
)


ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "data" / "retrieval_benchmark" / "corpus-fixtures.json"
QUERIES_PATH = ROOT / "data" / "retrieval_benchmark" / "evaluation-queries.json"


class RetrievalBenchmarkTests(unittest.TestCase):
    def test_project_fixtures_and_queries_validate_before_indexing(self):
        fixtures, queries = load_benchmark_inputs(CORPUS_PATH, QUERIES_PATH)

        self.assertGreaterEqual(len(fixtures), 7)
        self.assertGreaterEqual(len(queries), 6)
        for fixture in fixtures:
            self.assertRegex(fixture["id"], r"^di-rag-doc-[a-z0-9-]+$")
            self.assertEqual(fixture["content_origin"], "project-authored-fixture")
            self.assertNotIn("conversation", fixture["content"].casefold())
            self.assertIn("publisher", fixture)
            self.assertIn("official_url", fixture)
            self.assertTrue(fixture["topic_tags"])
            self.assertEqual(fixture["language"], "da")
            self.assertRegex(fixture["checked_at_utc"], r"^\d{4}-\d{2}-\d{2}T")
        for query in queries:
            self.assertRegex(query["id"], r"^di-rag-query-[a-z0-9-]+$")
            self.assertTrue(query["required_document_ids"])
            self.assertIsInstance(query["forbidden_document_ids"], list)
            self.assertTrue(query["allowed_source_health"])
            self.assertIn("metadata_filters", query)

    def test_invalid_fixture_fails_before_indexing_can_produce_result(self):
        with tempfile.TemporaryDirectory() as tempdir:
            corpus_path = Path(tempdir) / "corpus.json"
            queries_path = Path(tempdir) / "queries.json"
            output_path = Path(tempdir) / "result.json"
            corpus = [
                {
                    "id": "di-rag-doc-invalid",
                    "publisher": "SIRI",
                    "official_url": "https://www.nyidanmark.dk/example",
                    "topic_tags": ["permanent-residence"],
                    "language": "da",
                    "approval_state": "approved",
                    "source_health": "healthy",
                    "checked_at_utc": "2026-06-01T00:00:00Z",
                    "content_origin": "user-conversation",
                    "content": "Conversation transcript from a user.",
                }
            ]
            queries = [
                {
                    "id": "di-rag-query-invalid",
                    "query_text": "permanent ophold danskprøve",
                    "category": "exact-danish-terminology",
                    "required_document_ids": ["di-rag-doc-invalid"],
                    "forbidden_document_ids": [],
                    "allowed_source_health": ["healthy"],
                    "metadata_filters": {},
                }
            ]
            corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
            queries_path.write_text(json.dumps(queries), encoding="utf-8")

            with self.assertRaisesRegex(BenchmarkValidationError, "content_origin"):
                run_retrieval_benchmark(corpus_path, queries_path, output_path=output_path)

            self.assertFalse(output_path.exists())

    def test_runner_builds_fts5_index_and_reports_required_metrics(self):
        result = run_retrieval_benchmark(CORPUS_PATH, QUERIES_PATH)

        self.assertEqual(result["benchmark_id"], "mvp-retrieval-benchmark-issue-27")
        self.assertEqual(result["index"]["engine"], "sqlite-fts5")
        self.assertGreater(result["index"]["document_count"], 0)
        self.assertEqual(result["summary"]["query_count"], 6)
        self.assertIn("recall_at_1", result["summary"])
        self.assertIn("recall_at_3", result["summary"])
        self.assertIn("mean_reciprocal_rank", result["summary"])
        self.assertEqual(result["summary"]["forbidden_result_violations"], 0)
        self.assertEqual(result["summary"]["blocked_source_violations"], 0)
        self.assertIn("fixture_identity", result)
        self.assertRegex(result["executed_at_utc"], r"^\d{4}-\d{2}-\d{2}T")

    def test_metadata_eligibility_is_applied_before_crediting_results(self):
        result = run_retrieval_benchmark(CORPUS_PATH, QUERIES_PATH)
        by_id = {query["query_id"]: query for query in result["queries"]}

        blocked = by_id["di-rag-query-blocked-source-exclusion"]
        self.assertEqual(blocked["required_result_credit"], 0)
        self.assertIn(
            "di-rag-doc-changed-unreviewed-language-rule",
            blocked["blocked_result_ids"],
        )
        self.assertEqual(blocked["blocked_source_violations"], 0)

        stale = by_id["di-rag-query-stale-policy-usable"]
        self.assertEqual(stale["required_result_credit"], 1)
        self.assertIn("di-rag-doc-overdue-policy-usable-exam", stale["overdue_result_ids"])

    def test_query_categories_cover_issue_27_reviewed_cases(self):
        _, queries = load_benchmark_inputs(CORPUS_PATH, QUERIES_PATH)

        categories = {query["category"] for query in queries}

        self.assertEqual(
            categories,
            {
                "exact-danish-terminology",
                "terse-phrasing",
                "metadata-filtering",
                "stale-but-policy-usable",
                "blocked-source-exclusion",
                "forbidden-result-safety-boundary",
            },
        )

    def test_atomic_writers_do_not_leave_partial_results(self):
        with tempfile.TemporaryDirectory() as tempdir:
            output_path = Path(tempdir) / "benchmark.json"
            failure_path = Path(tempdir) / "benchmark.failed.json"
            result = run_retrieval_benchmark(CORPUS_PATH, QUERIES_PATH)
            write_benchmark_result(result, output_path)

            original = output_path.read_text(encoding="utf-8")
            write_benchmark_failure(
                BenchmarkValidationError("invalid query fixture"),
                failure_path,
            )

            self.assertEqual(output_path.read_text(encoding="utf-8"), original)
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
            self.assertEqual(failure["exit_status"], 1)
            self.assertIn("invalid query fixture", failure["diagnostic"])
            self.assertFalse(output_path.with_suffix(".json.tmp").exists())

    def test_build_fts_index_requires_sqlite_fts5(self):
        fixtures, _ = load_benchmark_inputs(CORPUS_PATH, QUERIES_PATH)

        connection = build_fts_index(fixtures)
        try:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'documents_fts'"
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
