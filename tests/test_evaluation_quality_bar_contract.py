import copy
import json
import unittest
from pathlib import Path
from urllib.parse import urlparse

from danish_rag.evaluation_quality_bar import (
    evaluation_case_assertion_specs,
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
    def test_cases_do_not_require_sources_outside_active_release_or_citations_for_clarification(self):
        cases = load_evaluation_cases(
            ROOT / "data/evaluation/evaluation-set-v0.1-candidate.json"
        )["cases"]
        manifest = json.loads(
            (
                ROOT
                / "data/knowledge_releases/kr-2026-07-06.1/manifest.json"
            ).read_text(encoding="utf-8")
        )
        active_domains = {
            urlparse(source["official_url"]).hostname.removeprefix("www.")
            for source in manifest["sources"]
        }

        for case in cases:
            with self.subTest(case=case["id"]):
                required_domains = set(
                    case["retrieval_expectations"]["required_source_domains"]
                )
                self.assertLessEqual(required_domains, active_domains)
                if case["final_answer_expectations"]["expected_behavior"] == "clarify":
                    self.assertEqual(
                        case["final_answer_expectations"]["required_citation_domains"],
                        [],
                    )

    def test_quality_bar_records_issue_7_release_blocking_scope(self):
        quality_bar = load_evaluation_quality_bar(QUALITY_BAR_PATH)

        self.assertEqual(
            quality_bar["quality_bar_id"],
            "mvp-evaluation-quality-bar-issue-7",
        )
        self.assertEqual(quality_bar["version"], "0.1.0-candidate")
        self.assertEqual(
            quality_bar["approval_status"],
            "approved",
        )
        self.assertEqual(
            quality_bar["approval_record"],
            "Product owner approval provided through the initiating GPT goal instruction on 2026-07-13.",
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

        self.assertEqual(cases["approval_status"], "approved")
        self.assertEqual(
            cases["approval_record"],
            "Product owner approval provided through the initiating GPT goal instruction on 2026-07-13.",
        )
        self.assertEqual(
            cases["assertion_contract"]["schema_version"],
            "evaluation-case-assertions-v1",
        )

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

            specs = evaluation_case_assertion_specs(case)
            expected_count = sum(
                len(case["final_answer_expectations"][field])
                for field in (
                    "required_facts",
                    "forbidden_claims",
                    "trust_indicators",
                    "privacy_requirements",
                )
            )
            self.assertEqual(len(specs), expected_count)
            self.assertEqual(len({item["assertion_id"] for item in specs}), expected_count)
            self.assertTrue(
                all(item["assertion_id"].startswith(f"{case['id']}:") for item in specs)
            )

        surfaces = {case["evaluation_surface"] for case in cases["cases"]}
        self.assertEqual(
            surfaces,
            {
                "answer-path",
                "source-policy-scenario",
                "browser-workflow",
                "knowledge-release-workflow",
                "provider-recovery-workflow",
            },
        )

    def test_evaluation_case_contract_rejects_missing_surface_and_drifted_assertion_schema(self):
        quality_bar = load_evaluation_quality_bar(QUALITY_BAR_PATH)
        cases = load_evaluation_cases(ROOT / quality_bar["evaluation_set"]["path"])

        missing_surface = copy.deepcopy(cases)
        del missing_surface["cases"][0]["evaluation_surface"]
        failures = validate_evaluation_cases(quality_bar, missing_surface)
        self.assertTrue(any("evaluation surface" in failure for failure in failures), failures)

        drifted_schema = copy.deepcopy(cases)
        drifted_schema["assertion_contract"]["schema_version"] = "unknown-v2"
        failures = validate_evaluation_cases(quality_bar, drifted_schema)
        self.assertTrue(any("assertion contract" in failure for failure in failures), failures)

        non_text_assertion = copy.deepcopy(cases)
        non_text_assertion["cases"][0]["final_answer_expectations"][
            "required_facts"
        ][0] = {"not": "approved prose"}
        failures = validate_evaluation_cases(quality_bar, non_text_assertion)
        self.assertTrue(any("non-empty text" in failure for failure in failures), failures)


if __name__ == "__main__":
    unittest.main()
