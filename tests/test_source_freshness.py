import json
import unittest
from pathlib import Path
from datetime import datetime, timezone
import tempfile

from danish_rag.knowledge_release import install_minimal_knowledge_release
from danish_rag.retrieval import HybridRetriever
import danish_rag.source_freshness as source_freshness_module
from danish_rag.source_freshness import assess_source_freshness
from tests.fixture_clock import (
    FIXTURE_EVALUATION_TIME_UTC,
    pin_freshness_clock,
    unpin_freshness_clock,
)
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


class FreshnessClockTests(unittest.TestCase):
    """#64: fixture freshness must not expire with the wall clock."""

    def test_suite_evaluates_freshness_at_the_pinned_fixture_time(self) -> None:
        self.assertEqual(
            source_freshness_module.datetime.now(timezone.utc), FIXTURE_EVALUATION_TIME_UTC
        )
        # The bundled fixture release's sources are due 2026-10-06T12:00:00Z.
        self.assertTrue(
            assess_source_freshness(source_evidence(due="2026-10-06T12:00:00Z")).answer_eligible
        )

    def test_every_wall_clock_freshness_input_is_pinned(self) -> None:
        # Callers that pass evaluated_at_utc get it from these clocks.
        import danish_rag.evidence_integrity as evidence_integrity
        import danish_rag.grounded_flexibility_evaluation as grounded_evaluation

        pinned = FIXTURE_EVALUATION_TIME_UTC.isoformat().replace("+00:00", "Z")
        self.assertEqual(evidence_integrity.utc_now_seconds(), pinned)
        self.assertEqual(grounded_evaluation._utc_now(), pinned)

    def test_pinned_time_precedes_every_fixture_release_review_due_date(self) -> None:
        releases = Path(__file__).resolve().parents[1] / "data" / "knowledge_releases"
        for manifest_path in releases.glob("*/manifest.json"):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            created = datetime.fromisoformat(manifest["created_at_utc"].replace("Z", "+00:00"))
            self.assertLessEqual(created, FIXTURE_EVALUATION_TIME_UTC, manifest_path.parent.name)
            for source in manifest["sources"]:
                due = (source.get("fresh_tomato_inputs") or {}).get("next_review_due_utc")
                if due and due > "2026-10-01":  # earlier dues are deliberately overdue fixtures
                    self.assertLess(
                        FIXTURE_EVALUATION_TIME_UTC,
                        datetime.fromisoformat(due.replace("Z", "+00:00")),
                        manifest_path.parent.name,
                    )

    def test_pinned_clock_keeps_real_datetimes_recognised(self) -> None:
        self.assertIsInstance(datetime(2026, 10, 7, tzinfo=timezone.utc), source_freshness_module.datetime)

    def test_unpinned_freshness_uses_the_wall_clock(self) -> None:
        # Due between the pinned time and today: eligible when pinned, overdue on the wall clock.
        evidence = source_evidence(due="2026-10-03T00:00:00Z")
        self.assertTrue(assess_source_freshness(evidence).answer_eligible)
        unpin_freshness_clock()
        try:
            self.assertIs(source_freshness_module.datetime, datetime)
            self.assertFalse(assess_source_freshness(evidence).answer_eligible)
        finally:
            pin_freshness_clock()


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
