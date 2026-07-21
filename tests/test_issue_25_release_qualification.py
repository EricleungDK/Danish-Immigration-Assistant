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
from danish_rag.source_registry import (
    assess_source_registry_qualification,
    load_source_registry,
)


ROOT = Path(__file__).resolve().parents[1]
QUALIFICATION_PATH = ROOT / "config" / "release-qualification.json"
DOC_PATH = ROOT / "docs" / "release-qualification.md"
RUNTIME_POLICY_PATH = ROOT / "config" / "runtime-policy.json"
QUALITY_BAR_PATH = ROOT / "config" / "evaluation-quality-bar.json"
SOURCE_REGISTRY_PATH = ROOT / "data" / "source_registry" / "sr-2026-07-06.1.json"
RUNTIME_PROBE_PATH = ROOT / "docs" / "progress" / "issue-26-runtime-probe.json"
HYBRID_RETRIEVAL_PATH = (
    ROOT / "docs" / "progress" / "issue-29-hybrid-retrieval-comparison.json"
)


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
            "approved",
        )
        self.assertEqual(
            qualification["quality_bar"]["approval_record"],
            "Product owner approval provided through the initiating GPT goal instruction on 2026-07-13.",
        )
        self.assertEqual(
            qualification["evaluation"]["dataset_id"],
            "di-rag-eval-set-v0.1-candidate",
        )
        self.assertEqual(qualification["evaluation"]["dataset_version"], "0.1.0-candidate")
        self.assertFalse(qualification["evaluation"]["uses_production_user_questions"])

        blocker_ids = {blocker["id"] for blocker in derive_release_blockers(qualification)}
        self.assertEqual(
            blocker_ids,
            {
                "final-answer-independent-human-adjudication",
                "production-source-registry-qualification",
                "supported-environment-critical-journeys",
                "browser-accessibility-suite",
                "production-release-owner-approval-pending",
            },
            blocker_ids,
        )
        self.assertNotIn("retrieval-required-evidence-baseline", blocker_ids)
        self.assertNotIn("full-release-evaluation-runner-not-implemented", blocker_ids)
        runner_gate = next(
            gate
            for gate in qualification["gate_results"]
            if gate["id"] == "release-evaluation-runner-report"
        )
        self.assertEqual(runner_gate["status"], "passed")
        retrieval_gate = next(
            gate
            for gate in qualification["gate_results"]
            if gate["id"] == "retrieval-required-evidence-baseline"
        )
        self.assertEqual(retrieval_gate["status"], "passed")
        self.assertEqual(retrieval_gate["observed"], 1.0)
        self.assertEqual(retrieval_gate["threshold"], 0.95)
        self.assertIn(
            "performance-runtime-and-indexing-baseline",
            qualification["evaluation"]["metrics_published"],
        )
        self.assertEqual(
            qualification["performance"]["threshold_status"],
            "not-applicable-no-numeric-sla-configured",
        )
        self.assertEqual(qualification["performance"]["measurement_status"], "passed")
        self.assertNotIn("quality-bar-human-approval-pending", blocker_ids)
        self.assertNotIn("issue-24-human-validation-pending", blocker_ids)
        self.assertNotIn("performance-thresholds-not-approved", blocker_ids)
        self.assertNotIn("performance-measurement-completeness", blocker_ids)

        gate_statuses = {
            gate["id"]: gate["status"] for gate in qualification["gate_results"]
        }
        self.assertEqual(gate_statuses["release-network-boundary-monitor"], "passed")
        self.assertEqual(
            gate_statuses["knowledge-release-rollback-fault-matrix"], "passed"
        )
        self.assertEqual(
            gate_statuses["supported-environment-critical-journeys"], "not_verified"
        )
        published_environment = next(
            environment
            for environment in qualification["supported_environment_matrix"]
            if "release_gate_status" in environment
        )
        self.assertEqual(published_environment["release_gate_status"], "not_verified")
        self.assertNotIn("passed", published_environment["status"])
        self.assertEqual(
            gate_statuses["performance-measurement-completeness"], "passed"
        )
        self.assertEqual(
            gate_statuses["browser-accessibility-suite"], "not_verified"
        )
        self.assertEqual(
            gate_statuses["final-answer-independent-human-adjudication"],
            "not_run",
        )
        self.assertEqual(
            gate_statuses["production-source-registry-qualification"], "blocked"
        )
        self.assertIn("npm install", qualification["distribution"]["install_steps"])
        self.assertIn("Node.js and npm", qualification["distribution"]["prerequisites"])
        self.assertEqual(validate_release_qualification(qualification), [])

    def test_supported_environment_matrix_cannot_contradict_release_gate(self):
        qualification = load_release_qualification(QUALIFICATION_PATH)
        drifted = copy.deepcopy(qualification)
        published_environment = next(
            environment
            for environment in drifted["supported_environment_matrix"]
            if "release_gate_status" in environment
        )
        published_environment["release_gate_status"] = "passed"
        published_environment["status"] = "critical-journeys-passed"

        failures = validate_release_qualification(drifted)

        self.assertTrue(
            any("matrix release status differs" in failure for failure in failures),
            failures,
        )
        self.assertTrue(
            any("must not claim passed journeys" in failure for failure in failures),
            failures,
        )

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

    def test_source_registry_gate_matches_fail_closed_production_assessment(self):
        qualification = load_release_qualification(QUALIFICATION_PATH)
        registry = load_source_registry(SOURCE_REGISTRY_PATH)
        assessment = assess_source_registry_qualification(registry)
        gate = next(
            gate
            for gate in qualification["gate_results"]
            if gate["id"] == "production-source-registry-qualification"
        )

        expected_status = (
            "passed" if assessment["production_release_eligible"] else "blocked"
        )
        self.assertEqual(gate["status"], expected_status)
        self.assertEqual(
            registry["production_qualification"]["status"], expected_status
        )
        self.assertEqual(
            gate["evidence"], SOURCE_REGISTRY_PATH.relative_to(ROOT).as_posix()
        )
        self.assertTrue(assessment["reason_codes"])

    def test_accessibility_gate_cannot_pass_without_manual_assistive_technology_evidence(self):
        qualification = load_release_qualification(QUALIFICATION_PATH)
        runtime_policy = load_runtime_policy(RUNTIME_POLICY_PATH)
        quality_bar = load_evaluation_quality_bar(QUALITY_BAR_PATH)
        drifted = copy.deepcopy(qualification)
        gate = next(
            item
            for item in drifted["gate_results"]
            if item["id"] == "browser-accessibility-suite"
        )
        gate["status"] = "passed"
        gate["observed"]["automated_suite_status"] = "current"

        failures = validate_release_qualification_sources(
            drifted,
            runtime_policy,
            quality_bar,
        )

        self.assertTrue(
            any("manual assistive-technology" in failure for failure in failures),
            failures,
        )

    def test_accessibility_gate_rejects_non_utc_evidence_timestamp(self):
        qualification = load_release_qualification(QUALIFICATION_PATH)
        runtime_policy = load_runtime_policy(RUNTIME_POLICY_PATH)
        quality_bar = load_evaluation_quality_bar(QUALITY_BAR_PATH)
        drifted = copy.deepcopy(qualification)
        gate = next(
            item
            for item in drifted["gate_results"]
            if item["id"] == "browser-accessibility-suite"
        )
        gate["status"] = "passed"
        gate["observed"].update(
            {
                "automated_suite_status": "current",
                "manual_assistive_technology_check": "passed",
            }
        )
        gate["manual_assistive_technology_evidence"] = {
            "path": "docs/progress/manual-at.json",
            "sha256": "a" * 64,
            "reviewer_id": "reviewer",
            "assistive_technology": "screen-reader version",
            "browser": "browser version",
            "tested_at_utc": "yesterday",
        }

        failures = validate_release_qualification_sources(
            drifted,
            runtime_policy,
            quality_bar,
        )

        self.assertTrue(
            any("invalid manual assistive-technology timestamp" in failure for failure in failures),
            failures,
        )

    def test_performance_gate_checks_measurements_without_invented_sla(self):
        qualification = load_release_qualification(QUALIFICATION_PATH)
        runtime_probe = json.loads(RUNTIME_PROBE_PATH.read_text(encoding="utf-8"))
        comparison = json.loads(HYBRID_RETRIEVAL_PATH.read_text(encoding="utf-8"))
        performance = qualification["performance"]
        measurements = performance["baseline_results"]
        dense_summary = comparison["candidates"]["dense"]["summary"]
        dense_operations = comparison["operations"]["dense"]

        self.assertEqual(
            measurements["structured_completion_ms"],
            runtime_probe["timings_ms"]["structured_completion"],
        )
        self.assertEqual(
            measurements["dense_mean_query_latency_ms"],
            dense_summary["latency_ms"]["mean"],
        )
        self.assertEqual(
            measurements["dense_mean_warm_retrieval_latency_ms"],
            dense_summary["warm_retrieval_latency_ms"]["mean"],
        )
        self.assertEqual(
            measurements["dense_indexing_wall_time_ms"],
            dense_operations["dense_indexing_wall_time_ms"],
        )
        self.assertEqual(
            measurements["dense_index_size_bytes"],
            dense_operations["dense_index_size_bytes"],
        )
        self.assertEqual(
            measurements["process_peak_resident_memory_mb"],
            dense_operations["process_peak_resident_memory_mb"],
        )
        self.assertEqual(performance["measurement_status"], "passed")
        self.assertEqual(
            performance["threshold_status"],
            "not-applicable-no-numeric-sla-configured",
        )
        self.assertNotIn("numeric_threshold", performance)

    def test_release_documentation_publishes_operating_privacy_model_corpus_update_recovery_and_support(self):
        qualification = load_release_qualification(QUALIFICATION_PATH)
        document_text = DOC_PATH.read_text(encoding="utf-8")

        self.assertEqual(
            validate_release_documentation_prose(qualification, document_text),
            [],
        )


if __name__ == "__main__":
    unittest.main()
