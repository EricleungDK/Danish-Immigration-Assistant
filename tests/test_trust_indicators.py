"""Trust indicators module (#71): health vocabulary, eligibility, confidence, score."""

import copy
import json
import re
import unittest
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from danish_rag import trust_indicators as ti
from danish_rag.trust_indicators import (
    CONFLICTING_AGREEMENT_STATES,
    SourceHealth,
    TrustLevel,
    assess_source_freshness,
    assess_trust_indicators,
    is_answer_eligible,
    is_conflicting_evidence,
    is_overdue_policy_usable,
    lowest_trust_level,
    no_material_source_trust_indicators,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
DUE = "2026-10-06T12:00:00Z"


def source(
    *,
    health: str = "healthy",
    review: str = "approved-current",
    due: str | None = DUE,
    blocked_after: str | None = None,
    **extra,
) -> dict:
    inputs: dict = {"source_health": "current"}
    if due:
        inputs["next_review_due_utc"] = due
    if blocked_after:
        inputs["overdue_blocked_after_utc"] = blocked_after
    return {
        "citation_id": extra.pop("citation_id", "c1"),
        "review_state": review,
        "source_health": health,
        "approval_state": "approved",
        "fresh_tomato_inputs": inputs,
        **extra,
    }


class SourceHealthVocabularyTests(unittest.TestCase):
    def test_values_are_the_stored_strings(self):
        self.assertEqual(
            {member.name: member.value for member in SourceHealth},
            {
                "HEALTHY": "healthy",
                "OVERDUE_POLICY_USABLE": "overdue-policy-usable",
                "CHANGED_UNREVIEWED": "changed-unreviewed",
                "BROKEN": "broken",
                "EXTRACTION_FAILED": "extraction-failed",
            },
        )

    def test_is_a_string_enum_equal_to_stored_strings(self):
        self.assertTrue(issubclass(SourceHealth, str))
        self.assertTrue(issubclass(SourceHealth, Enum))
        self.assertEqual(SourceHealth.HEALTHY, "healthy")
        self.assertEqual(
            json.dumps({"h": SourceHealth.OVERDUE_POLICY_USABLE}),
            '{"h": "overdue-policy-usable"}',
        )

    def test_stored_strings_round_trip(self):
        for member in SourceHealth:
            self.assertIs(SourceHealth(member.value), member)
            self.assertIs(SourceHealth(json.loads(json.dumps(member))), member)

    def test_bundled_signed_release_documents_still_read(self):
        # Stored material-source health in the signed releases' corpus documents.
        seen = set()
        for path in (ROOT / "data" / "knowledge_releases").glob("*/corpus/documents.json"):
            for document in json.loads(path.read_text(encoding="utf-8")):
                health = document["source_health"]
                seen.add(health)
                self.assertEqual(SourceHealth(health).value, health)
        self.assertIn("healthy", seen)

    def test_stored_strings_and_members_evaluate_identically(self):
        raw = source(health="overdue-policy-usable", review="overdue-policy-usable")
        typed = {**raw, "source_health": SourceHealth.OVERDUE_POLICY_USABLE}
        self.assertEqual(
            assess_source_freshness(raw, evaluated_at_utc=NOW),
            assess_source_freshness(typed, evaluated_at_utc=NOW),
        )

    def test_unknown_health_is_ineligible(self):
        self.assertFalse(is_answer_eligible(source(health="mystery"), evaluated_at_utc=NOW))

    def test_blocked_health_states_are_ineligible(self):
        for health in ("changed-unreviewed", "broken", "extraction-failed"):
            with self.subTest(health):
                assessment = assess_source_freshness(source(health=health), evaluated_at_utc=NOW)
                self.assertEqual((assessment.level, assessment.answer_eligible), ("Low", False))

    def test_overdue_policy_usable_predicate(self):
        self.assertTrue(is_overdue_policy_usable({"source_health": "overdue-policy-usable"}))
        self.assertTrue(
            is_overdue_policy_usable({"source_health": SourceHealth.OVERDUE_POLICY_USABLE})
        )
        self.assertFalse(is_overdue_policy_usable({"source_health": "healthy"}))
        self.assertFalse(is_overdue_policy_usable({}))


class DefinedOnceTests(unittest.TestCase):
    def test_conflicting_states(self):
        self.assertEqual(
            CONFLICTING_AGREEMENT_STATES,
            {"conflict", "conflicts", "conflicting", "contradicts"},
        )
        for state in CONFLICTING_AGREEMENT_STATES | {"CONFLICT"}:
            self.assertTrue(is_conflicting_evidence({"agreement_state": state}))
        self.assertTrue(is_conflicting_evidence({"conflicts_with_answer": True}))
        self.assertFalse(is_conflicting_evidence({}))
        self.assertFalse(is_conflicting_evidence({"agreement_state": "supports"}))

    def test_lowest_wins(self):
        low, med, high = TrustLevel.LOW, TrustLevel.MEDIUM, TrustLevel.HIGH
        self.assertIs(lowest_trust_level([high, med, high]), med)
        self.assertIs(lowest_trust_level([high, low, med]), low)
        self.assertIs(lowest_trust_level([high]), high)
        self.assertIs(lowest_trust_level([]), low)

    def test_vocabulary_has_one_definition_in_the_package(self):
        for pattern in (
            r"CONFLICTING_AGREEMENT_STATES\s*=",
            r"class TrustLevel\b",
            r"def lowest_trust_level",
            r"class SourceHealth\b",
        ):
            owners = [
                path.name
                for path in (ROOT / "danish_rag").glob("*.py")
                if re.search(pattern, path.read_text(encoding="utf-8"))
            ]
            self.assertEqual(owners, ["trust_indicators.py"], pattern)

    def test_answer_path_and_retrieval_do_not_hand_match_health_strings(self):
        for name in ("answer_pipeline.py", "retrieval.py"):
            text = (ROOT / "danish_rag" / name).read_text(encoding="utf-8")
            for literal in ("overdue-policy-usable", '"healthy"', "'healthy'", "approved-current"):
                self.assertNotIn(literal, text, f"{name}: {literal}")

    def test_source_freshness_module_is_absorbed(self):
        self.assertFalse((ROOT / "danish_rag" / "source_freshness.py").exists())
        with self.assertRaises(ImportError):
            __import__("danish_rag.source_freshness")


class PureFunctionTests(unittest.TestCase):
    def test_evaluation_time_is_required_and_tz_aware(self):
        evidence = [source()]
        with self.assertRaises(TypeError):
            assess_trust_indicators(
                official_fact_count=1, cited_official_fact_count=1, used_evidence=evidence
            )
        with self.assertRaises(ValueError):
            assess_trust_indicators(
                official_fact_count=1,
                cited_official_fact_count=1,
                used_evidence=evidence,
                evaluated_at=datetime(2026, 10, 1),
            )
        with self.assertRaises(TypeError):
            is_answer_eligible(source())

    def test_inputs_are_not_mutated_and_result_is_repeatable(self):
        evidence = [source(), source(citation_id="c2", agreement_state="supports")]
        before = copy.deepcopy(evidence)
        kwargs = dict(
            official_fact_count=2,
            cited_official_fact_count=2,
            used_evidence=evidence,
            evaluated_at=NOW,
        )
        first = assess_trust_indicators(**kwargs)
        second = assess_trust_indicators(**kwargs)
        self.assertEqual(evidence, before)
        self.assertEqual(first, second)

    def test_complete_coverage_current_sources_is_high_high(self):
        trust = assess_trust_indicators(
            official_fact_count=2,
            cited_official_fact_count=2,
            used_evidence=[source()],
            evaluated_at=NOW,
        )
        self.assertEqual(
            trust,
            {
                "evidence_confidence": "High",
                "evidence_confidence_reason": (
                    "Evidence coverage is complete for 2 official fact(s); cited material "
                    "sources are attached directly to the claims and no retrieved material "
                    "source is marked as conflicting."
                ),
                "fresh_tomato_score": "High",
                "fresh_tomato_reason": (
                    "Source freshness is high because all material sources are current "
                    "and healthy."
                ),
            },
        )

    def test_no_official_fact_is_low_confidence(self):
        trust = assess_trust_indicators(
            official_fact_count=0,
            cited_official_fact_count=0,
            used_evidence=[source()],
            evaluated_at=NOW,
        )
        self.assertEqual(trust["evidence_confidence"], "Low")
        self.assertEqual(
            trust["evidence_confidence_reason"],
            "No official fact was produced; the response is limited to an "
            "evidence-bounded refusal or source warning.",
        )

    def test_partial_coverage_is_low_confidence(self):
        trust = assess_trust_indicators(
            official_fact_count=3,
            cited_official_fact_count=2,
            used_evidence=[source()],
            evaluated_at=NOW,
        )
        self.assertEqual(trust["evidence_confidence"], "Low")
        self.assertEqual(
            trust["evidence_confidence_reason"],
            "Evidence coverage is incomplete: 2 of 3 official fact(s) have adjacent "
            "approved-source citations.",
        )

    def test_conflict_lowers_confidence_but_not_freshness(self):
        trust = assess_trust_indicators(
            official_fact_count=1,
            cited_official_fact_count=1,
            used_evidence=[source(), source(citation_id="b", agreement_state="conflicts")],
            evaluated_at=NOW,
        )
        self.assertEqual(trust["evidence_confidence"], "Low")
        self.assertEqual(trust["fresh_tomato_score"], "High")
        self.assertEqual(
            trust["evidence_confidence_reason"],
            "Evidence agreement is incomplete: conflicting material source(s) were "
            "retrieved for this answer (b).",
        )

    def test_lowest_material_source_wins_for_fresh_tomato(self):
        overdue = source(
            citation_id="o", health="overdue-policy-usable", review="overdue-policy-usable"
        )
        kwargs = dict(official_fact_count=1, cited_official_fact_count=1, evaluated_at=NOW)
        trust = assess_trust_indicators(used_evidence=[source(), overdue], **kwargs)
        self.assertEqual(trust["fresh_tomato_score"], "Medium")
        self.assertEqual(
            trust["fresh_tomato_reason"],
            "Source freshness is medium because the lowest material-source freshness "
            "score is Medium.",
        )
        blocked = source(citation_id="x", health="broken")
        trust = assess_trust_indicators(used_evidence=[source(), overdue, blocked], **kwargs)
        self.assertEqual(trust["fresh_tomato_score"], "Low")
        self.assertEqual(
            trust["fresh_tomato_reason"],
            "Source freshness is low because no current healthy material source could "
            "be attached.",
        )

    def test_same_evidence_changes_only_with_evaluation_time(self):
        kwargs = dict(
            official_fact_count=1, cited_official_fact_count=1, used_evidence=[source(due=DUE)]
        )
        before = assess_trust_indicators(**kwargs, evaluated_at=NOW)
        after = assess_trust_indicators(
            **kwargs, evaluated_at=datetime(2026, 10, 7, tzinfo=timezone.utc)
        )
        self.assertEqual(before["fresh_tomato_score"], "High")
        self.assertEqual(after["fresh_tomato_score"], "Low")

    def test_no_sources_is_low_freshness(self):
        trust = assess_trust_indicators(
            official_fact_count=0,
            cited_official_fact_count=0,
            used_evidence=[],
            evaluated_at=NOW,
        )
        self.assertEqual(trust["fresh_tomato_score"], "Low")

    def test_refusal_indicators(self):
        self.assertEqual(
            no_material_source_trust_indicators(),
            {
                "evidence_confidence": "Low",
                "evidence_confidence_reason": (
                    "No eligible approved official material source could support a "
                    "substantive answer."
                ),
                "fresh_tomato_score": "Low",
                "fresh_tomato_reason": (
                    "Source freshness is low because no current healthy material source "
                    "could be attached."
                ),
            },
        )

    def test_module_never_reads_the_wall_clock(self):
        self.assertNotIn("now(", Path(ti.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
