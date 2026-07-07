import copy
import json
import unittest
from pathlib import Path

from danish_rag.release_qualification import (
    derive_release_blockers,
    extract_documented_release_contract,
    load_release_qualification,
    validate_release_document_contract,
    validate_release_documentation_prose,
    validate_release_qualification,
    validate_release_qualification_sources,
)
from danish_rag.evaluation_quality_bar import load_evaluation_quality_bar
from danish_rag.runtime_policy import load_runtime_policy


ROOT = Path(__file__).resolve().parents[1]
QUALIFICATION_PATH = ROOT / "config" / "release-qualification.json"
DOC_PATH = ROOT / "docs" / "release-qualification.md"
RUNTIME_POLICY_PATH = ROOT / "config" / "runtime-policy.json"
QUALITY_BAR_PATH = ROOT / "config" / "evaluation-quality-bar.json"


class Issue25ReleaseQualificationTests(unittest.TestCase):
    def test_release_qualification_reports_blocked_release_without_weakening_gates(self):
        qualification = load_release_qualification(QUALIFICATION_PATH)

        self.assertEqual(
            qualification["qualification_id"],
            "mvp-release-qualification-issue-25",
        )
        self.assertEqual(qualification["qualification_status"], "blocked")
        self.assertEqual(qualification["release_decision"], "do-not-release")
        self.assertEqual(qualification["quality_bar"]["version"], "0.1.0-candidate")
        self.assertEqual(
            qualification["quality_bar"]["approval_status"],
            "candidate-ready-for-human-approval",
        )
        self.assertEqual(
            qualification["evaluation"]["dataset_id"],
            "di-rag-eval-set-v0.1-candidate",
        )
        self.assertEqual(qualification["evaluation"]["dataset_version"], "0.1.0-candidate")
        self.assertFalse(qualification["evaluation"]["uses_production_user_questions"])

        blocker_ids = {blocker["id"] for blocker in derive_release_blockers(qualification)}
        self.assertTrue(
            {
                "quality-bar-human-approval-pending",
                "retrieval-baseline-below-release-threshold",
                "full-release-evaluation-runner-not-implemented",
                "environment-matrix-critical-journeys-not-complete",
                "performance-thresholds-not-approved",
                "issue-24-human-validation-pending",
            }.issubset(blocker_ids),
            blocker_ids,
        )
        self.assertIn(
            "performance-runtime-and-indexing-baseline",
            qualification["evaluation"]["metrics_published"],
        )
        self.assertEqual(
            qualification["performance"]["threshold_status"],
            "pending-human-approval",
        )
        self.assertEqual(validate_release_qualification(qualification), [])

    def test_blocking_conditions_prevent_release_even_if_manual_status_drifts(self):
        qualification = load_release_qualification(QUALIFICATION_PATH)
        drifted = copy.deepcopy(qualification)
        drifted["qualification_status"] = "qualified"
        drifted["release_decision"] = "release"
        drifted["explicit_release_blockers"] = []
        drifted["gate_results"] = []
        drifted["human_approval_records"] = []
        drifted["blocking_conditions"][0]["observed_count"] = 1

        blockers = derive_release_blockers(drifted)

        self.assertTrue(
            any(blocker["id"] == "uncited-official-fact" for blocker in blockers),
            blockers,
        )
        self.assertTrue(
            any("must remain blocked" in failure for failure in validate_release_qualification(drifted)),
        )

    def test_documentation_embeds_matching_release_qualification_contract(self):
        qualification = load_release_qualification(QUALIFICATION_PATH)
        documented_contract = extract_documented_release_contract(DOC_PATH)

        self.assertEqual(documented_contract, qualification["documentation_contract"])
        self.assertEqual(
            validate_release_document_contract(qualification, documented_contract),
            [],
        )
        json.dumps(documented_contract)

    def test_release_qualification_matches_runtime_and_quality_bar_source_contracts(self):
        qualification = load_release_qualification(QUALIFICATION_PATH)
        runtime_policy = load_runtime_policy(RUNTIME_POLICY_PATH)
        quality_bar = load_evaluation_quality_bar(QUALITY_BAR_PATH)

        self.assertEqual(
            validate_release_qualification_sources(
                qualification,
                runtime_policy,
                quality_bar,
            ),
            [],
        )

        drifted = copy.deepcopy(qualification)
        drifted["runtime"]["generation_model"] = "different-local-model"

        failures = validate_release_qualification_sources(
            drifted,
            runtime_policy,
            quality_bar,
        )

        self.assertTrue(
            any("generation model" in failure for failure in failures),
            failures,
        )

    def test_release_documentation_publishes_operating_privacy_model_corpus_update_recovery_and_support(self):
        qualification = load_release_qualification(QUALIFICATION_PATH)
        document_text = DOC_PATH.read_text(encoding="utf-8")

        self.assertEqual(
            validate_release_documentation_prose(qualification, document_text),
            [],
        )


if __name__ == "__main__":
    unittest.main()
