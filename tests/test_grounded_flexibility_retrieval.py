import json
import unittest

from danish_rag.grounded_flexibility_retrieval import DEFAULT_DATASET, evaluate_retrieval


class RetrievalEvidenceTests(unittest.TestCase):
    def test_chunks_receive_source_credit_without_exporting_content(self):
        class Retriever:
            def retrieve(self, question, *, limit):
                return [
                    {
                        "document_id": "private-chunk-id",
                        "source_document_id": document,
                        "content": "PRIVATE ANSWER TEXT",
                        "query": question,
                        "review_state": "approved-current",
                        "source_health": "healthy",
                        "last_checked_at_utc": "2026-09-05T00:00:00Z",
                        "fresh_tomato_inputs": {"next_review_due_utc": "2099-01-01T00:00:00Z"},
                    }
                    for document in (
                        "di-rag-doc-permanent-residence-language",
                        "di-rag-doc-registration-deadlines-2026",
                    )
                ]

        report = evaluate_retrieval(DEFAULT_DATASET, retriever=Retriever())
        self.assertTrue(report["strict_passed"])
        self.assertEqual(report["semantic_qualification"], "not_evaluated")
        serialized = json.dumps(report)
        self.assertNotIn("PRIVATE ANSWER", serialized)
        self.assertNotIn("private-chunk-id", serialized)
        with open(DEFAULT_DATASET) as source:
            for case in json.load(source)["cases"]:
                self.assertNotIn(case["prompt"], serialized)

    def test_provider_errors_remain_blocking_without_leaking_exception_text(self):
        class Retriever:
            def retrieve(self, question, *, limit):
                raise RuntimeError("PRIVATE PROMPT " + question)

        report = evaluate_retrieval(DEFAULT_DATASET, retriever=Retriever())
        self.assertFalse(report["strict_passed"])
        self.assertEqual(report["execution_error_count"], report["case_count"])
        self.assertIn("retrieval_execution_errors", report["threshold_failures"])
        self.assertNotIn("PRIVATE PROMPT", json.dumps(report))

    def test_blocked_sources_cannot_receive_coverage_credit(self):
        class Retriever:
            def retrieve(self, question, *, limit):
                return [{
                    "document_id": "di-rag-doc-permanent-residence-language",
                    "review_state": "withdrawn",
                    "source_health": "healthy",
                }]

        report = evaluate_retrieval(DEFAULT_DATASET, retriever=Retriever())
        self.assertFalse(report["strict_passed"])
        self.assertEqual(report["blocked_source_violations"], report["case_count"])
        self.assertTrue(all(value["covered"] == 0 for value in report["by_intent"].values()))
