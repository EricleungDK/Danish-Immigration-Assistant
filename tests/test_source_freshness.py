import unittest
import tempfile

from danish_rag.knowledge_release import install_minimal_knowledge_release
from danish_rag.retrieval import HybridRetriever
from danish_rag.source_freshness import assess_source_freshness
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture


def source_evidence(
    *,
    due: str,
    blocked_after: str | None = None,
) -> dict[str, object]:
    inputs: dict[str, object] = {
        "source_health": "current",
        "next_review_due_utc": due,
    }
    if blocked_after is not None:
        inputs["overdue_blocked_after_utc"] = blocked_after
    return {
        "review_state": "approved-current",
        "source_health": "healthy",
        "approval_state": "approved",
        "fresh_tomato_inputs": inputs,
    }


class SourceFreshnessTests(unittest.TestCase):
    def test_production_retrieval_attaches_manifest_freshness_inputs(self) -> None:
        provider = DeterministicEmbeddingProviderFixture()
        with tempfile.TemporaryDirectory() as data_dir:
            install_minimal_knowledge_release(
                data_dir,
                embedding_provider=provider,
            )
            evidence = HybridRetriever.from_data_dir(
                data_dir,
                embedding_provider=provider,
            ).retrieve("What Danish test is needed for permanent residence?")[0]

        self.assertEqual(
            evidence["fresh_tomato_inputs"]["next_review_due_utc"],
            "2026-10-06T12:00:00Z",
        )
        self.assertEqual(evidence["fresh_tomato_inputs"]["source_health"], "current")

    def test_current_review_is_high(self) -> None:
        assessment = assess_source_freshness(
            source_evidence(due="2026-08-01T00:00:00Z"),
            evaluated_at_utc="2026-07-14T00:00:00Z",
        )

        self.assertEqual(assessment.level, "High")
        self.assertTrue(assessment.answer_eligible)

    def test_review_due_date_lowers_score_without_changing_evidence_confidence(self) -> None:
        assessment = assess_source_freshness(
            source_evidence(
                due="2026-07-01T00:00:00Z",
                blocked_after="2026-08-01T00:00:00Z",
            ),
            evaluated_at_utc="2026-07-14T00:00:00Z",
        )

        self.assertEqual(assessment.level, "Medium")
        self.assertTrue(assessment.answer_eligible)
        self.assertIn("overdue", assessment.reason.casefold())

    def test_block_after_date_makes_source_ineligible_and_low(self) -> None:
        assessment = assess_source_freshness(
            source_evidence(
                due="2026-06-01T00:00:00Z",
                blocked_after="2026-07-01T00:00:00Z",
            ),
            evaluated_at_utc="2026-07-14T00:00:00Z",
        )

        self.assertEqual(assessment.level, "Low")
        self.assertFalse(assessment.answer_eligible)
        self.assertIn("blocked", assessment.reason.casefold())


if __name__ == "__main__":
    unittest.main()
