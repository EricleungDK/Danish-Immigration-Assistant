import copy
import json
import unittest
from pathlib import Path

from danish_rag.evaluation_quality_bar import (
    extract_documented_quality_bar_contract,
    load_evaluation_cases,
    load_evaluation_quality_bar,
    validate_evaluation_cases,
    validate_quality_bar_document_contract,
    validate_release_thresholds,
)


ROOT = Path(__file__).resolve().parents[1]
QUALITY_BAR_PATH = ROOT / "config" / "evaluation-quality-bar.json"
DOC_PATH = ROOT / "docs" / "evaluation-quality-bar.md"


class EvaluationQualityBarContractTests(unittest.TestCase):
    def test_quality_bar_records_issue_7_release_blocking_scope(self):
        quality_bar = load_evaluation_quality_bar(QUALITY_BAR_PATH)

        self.assertEqual(
            quality_bar["quality_bar_id"],
            "mvp-evaluation-quality-bar-issue-7",
        )
        self.assertEqual(quality_bar["version"], "0.1.0-candidate")
        self.assertEqual(
            quality_bar["approval_status"],
            "candidate-ready-for-human-approval",
        )
        self.assertEqual(
            quality_bar["prd_user_stories"],
            list(range(75, 87)),
        )
        self.assertEqual(
            quality_bar["runtime_baseline"]["baseline_id"],
            "mvp-runtime-baseline-issue-26",
        )
        self.assertEqual(
            quality_bar["retrieval_baseline"]["selected_candidate"],
            "hybrid",
        )

        metric_ids = {metric["id"] for metric in quality_bar["metrics"]}
        self.assertTrue(
            {
                "retrieval-required-evidence-recall-at-3",
                "retrieval-blocked-source-violations",
                "official-fact-citation-coverage",
                "unsupported-claim-rate",
                "clarify-answer-refuse-behavior",
                "trust-indicator-correctness",
                "privacy-network-boundary",
                "update-rollback-success",
                "accessibility-conformance",
                "reliability-critical-journeys",
            }.issubset(metric_ids)
        )

    def test_release_thresholds_reject_silent_weakening(self):
        quality_bar = load_evaluation_quality_bar(QUALITY_BAR_PATH)
        self.assertEqual(validate_release_thresholds(quality_bar), [])

        weakened = copy.deepcopy(quality_bar)
        weakened["thresholds"]["final_answer"]["unsupported_claim_rate_max"] = 0.01

        failures = validate_release_thresholds(weakened)

        self.assertTrue(
            any("unsupported claim" in failure for failure in failures),
            failures,
        )

    def test_documentation_embeds_matching_quality_bar_contract(self):
        quality_bar = load_evaluation_quality_bar(QUALITY_BAR_PATH)
        documented_contract = extract_documented_quality_bar_contract(DOC_PATH)

        self.assertEqual(documented_contract, quality_bar["documentation_contract"])
        self.assertEqual(
            validate_quality_bar_document_contract(quality_bar, documented_contract),
            [],
        )
        json.dumps(documented_contract)

    def test_candidate_evaluation_cases_keep_retrieval_and_answer_separate(self):
        quality_bar = load_evaluation_quality_bar(QUALITY_BAR_PATH)
        cases = load_evaluation_cases(ROOT / quality_bar["evaluation_set"]["path"])

        self.assertEqual(validate_evaluation_cases(quality_bar, cases), [])
        self.assertEqual(len(cases["cases"]), quality_bar["evaluation_set"]["case_count"])

        behavior_classes = {case["behavior_class"] for case in cases["cases"]}
        self.assertTrue(
            {
                "happy_path",
                "edge_case",
                "out_of_bounds",
                "ambiguity",
                "conflict",
                "stale_source",
                "refusal",
                "robustness",
            }.issubset(behavior_classes)
        )

        for case in cases["cases"]:
            self.assertIn("retrieval_expectations", case)
            self.assertIn("final_answer_expectations", case)
            self.assertIsInstance(
                case["retrieval_expectations"]["required_facts"],
                list,
            )
            self.assertIsInstance(
                case["final_answer_expectations"]["required_facts"],
                list,
            )
            self.assertIn(
                case["final_answer_expectations"]["expected_behavior"],
                {"answer", "clarify", "refuse", "answer-with-refusal"},
            )


if __name__ == "__main__":
    unittest.main()
