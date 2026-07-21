from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from danish_rag.evaluation_machine_evidence import (
    BROWSER_WORKFLOW_REPORT_SCHEMA_VERSION,
    MachineEvidenceError,
    PROVIDER_RECOVERY_REPORT_SCHEMA_VERSION,
    build_automated_workflow_evidence,
    run_provider_recovery_execution,
    run_source_policy_scenario,
)
from danish_rag.evaluation_quality_bar import load_evaluation_cases
from danish_rag.final_answer_evaluation import (
    CaseExecution,
    FinalAnswerEvaluationError,
    generate_final_answer_evaluation,
    main,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "data/evaluation/evaluation-set-v0.1-candidate.json"

_BROWSER_OBSERVATIONS_BY_CASE = {
    "eval-015-update-telemetry-privacy": [
        "active_corpus_identity_visible",
        "availability_without_install",
        "no_automatic_install",
        "separate_download_and_install_approval",
    ],
    "eval-016-keyboard-evidence-drawer": [
        "assistive_provenance_and_trust_text",
        "dialog_focus_and_return",
        "drawer_open_has_no_request",
        "evidence_confidence_text_visible",
        "fresh_tomato_text_visible",
        "keyboard_controls_reachable",
        "no_unintended_focus_trap",
        "trust_has_text_labels",
    ],
    "eval-017-responsive-reduced-motion": [
        "evidence_confidence_text_visible",
        "fresh_tomato_text_visible",
        "narrow_core_controls_visible",
        "narrow_core_workflow_usable",
        "reduced_motion_preserves_status",
        "responsive_requests_are_loopback_only",
        "trust_and_status_use_text",
        "two_hundred_percent_no_horizontal_overflow",
    ],
    "eval-019-runtime-identity-visible": [
        "corpus_identity_visible",
        "historical_provenance_unchanged_after_update",
        "identity_display_excludes_secrets",
        "identity_urls_and_ui_exclude_secrets",
        "model_and_source_labels_distinct",
        "model_identity_visible",
        "provider_identity_visible",
        "provider_model_corpus_check_date_visible",
    ],
}


class _FailingAnswerRunner:
    supported_evaluation_surfaces = {"answer-path"}
    public_identity = {
        "provider_id": "machine-evidence-test",
        "model": "machine-evidence-test",
        "corpus_id": "machine-evidence-test",
    }

    def run(self, case):
        return CaseExecution(
            case_id=case["id"],
            result=None,
            evidence=[],
            error_type="DeliberateAnswerPathFailure",
        )


def _dataset_cases() -> dict[str, dict]:
    dataset = load_evaluation_cases(DATASET_PATH)
    return {case["id"]: case for case in dataset["cases"]}


def _release_monitor_report() -> dict:
    phases = [
        "verification",
        "extraction",
        "embedding",
        "indexing",
        "activation",
        "late_activation",
    ]
    return {
        "schema_version": "1.0",
        "mode": "live",
        "component_passed": True,
        "strict_passed": True,
        "privacy": {
            "monitor_id": "release-network-boundary-monitor",
            "mode": "live",
            "passed": True,
            "failures": [],
            "forbidden_request_count": 0,
            "observed_workflows": ["knowledge_update_review"],
            "release_request_inspection": {
                "approved_operation": True,
                "content_free": True,
                "field_names": [
                    "active_knowledge_release_id",
                    "application_version",
                    "operation",
                ],
            },
        },
        "rollback": {
            "monitor_id": "knowledge-release-rollback-fault-matrix",
            "mode": "live",
            "passed": True,
            "failures": [],
            "prior_pair_identity": {
                "knowledge_release_id": "kr-prior",
                "corpus_id": "kr-prior",
            },
            "results": [
                {
                    "phase": phase,
                    "status": "passed",
                    "failure_observed": True,
                    "prior_pair_unchanged": True,
                    "prior_pair_queryable": True,
                    "target_release_active": False,
                    "installation_reported_success": False,
                    "signature_verification_passed": True,
                }
                for phase in phases
            ],
        },
        "supported_environment": {
            "monitor_id": "supported-environment-critical-journeys",
            "mode": "live",
            "passed": True,
            "can_qualify_supported_environment": True,
            "live_provider_calls": True,
            "journeys": [
                {"id": "update-installation", "status": "passed"},
                {"id": "rollback", "status": "passed"},
            ],
        },
    }


def _browser_workflow_report() -> dict:
    spec = ROOT / "tests/browser/zz_evaluation_workflows.spec.js"
    return {
        "schema_version": BROWSER_WORKFLOW_REPORT_SCHEMA_VERSION,
        "command": [
            "node_modules/.bin/playwright",
            "test",
            "tests/browser/zz_evaluation_workflows.spec.js",
            "--reporter=json",
        ],
        "exit_status": 0,
        "raw_output_sha256": "1" * 64,
        "test_output_sensitive_content_absent": True,
        "test_source": {
            "path": "tests/browser/zz_evaluation_workflows.spec.js",
            "sha256": hashlib.sha256(spec.read_bytes()).hexdigest(),
        },
        "tests": [
            {
                "test_id": "eval-015-update-telemetry-privacy",
                "status": "passed",
                "observations": _BROWSER_OBSERVATIONS_BY_CASE[
                    "eval-015-update-telemetry-privacy"
                ],
            },
            {
                "test_id": "eval-016-keyboard-evidence-drawer",
                "status": "passed",
                "observations": _BROWSER_OBSERVATIONS_BY_CASE[
                    "eval-016-keyboard-evidence-drawer"
                ],
            },
            {
                "test_id": "eval-017-responsive-reduced-motion",
                "status": "passed",
                "observations": _BROWSER_OBSERVATIONS_BY_CASE[
                    "eval-017-responsive-reduced-motion"
                ],
            },
            {
                "test_id": "eval-019-runtime-identity-visible",
                "status": "passed",
                "observations": _BROWSER_OBSERVATIONS_BY_CASE[
                    "eval-019-runtime-identity-visible"
                ],
            },
        ],
    }


def _provider_recovery_report() -> dict:
    return {
        "schema_version": PROVIDER_RECOVERY_REPORT_SCHEMA_VERSION,
        "command": "in-process ASGI production workflow",
        "exit_status": 0,
        "case_id": "eval-018-provider-unavailable-recovery",
        "observations": {
            "http_status_is_503": True,
            "actionable_local_recovery_guidance_visible": True,
            "question_preserved_for_retry": True,
            "prior_record_unchanged": True,
            "provider_identity_visible": True,
            "model_identity_visible": True,
            "secret_markers_absent": True,
            "partial_answer_not_saved": True,
            "remote_fallback_request_count": 0,
        },
    }


class SourcePolicyScenarioEvidenceTests(unittest.TestCase):
    def test_all_four_source_policy_scenarios_execute_production_answer_policy(self):
        cases = _dataset_cases()

        evidence = [
            run_source_policy_scenario(cases[case_id])
            for case_id in (
                "eval-010-conflicting-official-sources",
                "eval-011-overdue-policy-usable-source",
                "eval-012-changed-source-blocked",
                "eval-013-retrieval-miss-cannot-be-masked",
            )
        ]

        self.assertEqual(
            [item["observed_behavior"] for item in evidence],
            ["answer-with-refusal", "answer-with-refusal", "answer-with-refusal", "refuse"],
        )
        for item in evidence:
            with self.subTest(case_id=item["case_id"]):
                self.assertEqual(item["assessment_method"], "automated-production-scenario")
                self.assertTrue(item["scenario_passed"])
                self.assertTrue(item["assertion_results"])
                self.assertEqual(set(item["assertion_results"].values()), {"passed"})
                self.assertEqual(len(item["execution_sha256"]), 64)
                serialized = json.dumps(item, sort_keys=True).casefold()
                self.assertNotIn("one official page says", serialized)
                self.assertNotIn("just confirm it quickly", serialized)

        by_id = {item["case_id"]: item for item in evidence}
        self.assertEqual(
            by_id["eval-010-conflicting-official-sources"]["source_policy"][
                "material_citation_count"
            ],
            2,
        )
        self.assertEqual(
            by_id["eval-011-overdue-policy-usable-source"]["trust"][
                "fresh_tomato_score"
            ],
            "Medium",
        )
        self.assertEqual(
            by_id["eval-012-changed-source-blocked"]["source_policy"][
                "blocked_source_citation_count"
            ],
            0,
        )
        self.assertEqual(
            by_id["eval-013-retrieval-miss-cannot-be-masked"]["source_policy"][
                "generator_call_count"
            ],
            0,
        )

    def test_source_policy_assertions_fail_closed_if_approved_criterion_drifts(self):
        case = json.loads(
            json.dumps(_dataset_cases()["eval-010-conflicting-official-sources"])
        )
        case["final_answer_expectations"]["required_facts"][0] = "Changed criterion"

        with self.assertRaisesRegex(MachineEvidenceError, "criterion contract"):
            run_source_policy_scenario(case)

    def test_final_evaluator_runs_source_policy_surface_without_human_adjudication(self):
        report = generate_final_answer_evaluation(
            ROOT,
            runner=_FailingAnswerRunner(),
            mode="controlled",
            generated_at_utc="2026-07-14T17:00:00Z",
        )

        by_id = {item["case_id"]: item for item in report["case_results"]}
        for case_id in (
            "eval-010-conflicting-official-sources",
            "eval-011-overdue-policy-usable-source",
            "eval-012-changed-source-blocked",
            "eval-013-retrieval-miss-cannot-be-masked",
        ):
            with self.subTest(case_id=case_id):
                self.assertEqual(by_id[case_id]["status"], "passed")
                self.assertTrue(by_id[case_id]["evaluation_completed"])
                self.assertEqual(
                    by_id[case_id]["assessment_method"],
                    "automated-production-scenario",
                )
                self.assertEqual(
                    by_id[case_id]["checks"]["required_facts"]["status"],
                    "passed",
                )
        self.assertEqual(report["execution"]["answer_case_execution_count"], 10)


class AutomatedWorkflowEvidenceTests(unittest.TestCase):
    def _write_inputs(self, root: Path) -> tuple[Path, Path, Path]:
        release = root / "release-monitors.json"
        browser = root / "browser-workflows.json"
        provider = root / "provider-recovery.json"
        release.write_text(json.dumps(_release_monitor_report()), encoding="utf-8")
        browser.write_text(json.dumps(_browser_workflow_report()), encoding="utf-8")
        provider.write_text(json.dumps(_provider_recovery_report()), encoding="utf-8")
        return release, browser, provider

    def test_builder_creates_assertion_specific_hash_bound_bundle_for_six_workflows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release, browser, provider = self._write_inputs(root)
            output_dir = root / "evidence"

            bundle = build_automated_workflow_evidence(
                repo_root=ROOT,
                output_dir=output_dir,
                release_monitor_path=release,
                browser_workflow_report_path=browser,
                provider_recovery_report_path=provider,
                generated_at_utc="2026-07-14T17:00:00Z",
            )

            self.assertEqual(bundle["schema_version"], "final-answer-adjudications-v1")
            self.assertEqual(
                [item["case_id"] for item in bundle["cases"]],
                [
                    "eval-015-update-telemetry-privacy",
                    "eval-016-keyboard-evidence-drawer",
                    "eval-017-responsive-reduced-motion",
                    "eval-018-provider-unavailable-recovery",
                    "eval-019-runtime-identity-visible",
                    "eval-020-update-rollback",
                ],
            )
            for adjudication in bundle["cases"]:
                with self.subTest(case_id=adjudication["case_id"]):
                    self.assertEqual(
                        adjudication["assessment_method"], "automated-workflow-test"
                    )
                    self.assertEqual(set(adjudication["assertion_results"].values()), {"passed"})
                    artifact_path = Path(adjudication["evidence_binding"]["path"])
                    self.assertTrue(artifact_path.is_file())
                    self.assertEqual(
                        hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                        adjudication["evidence_binding"]["sha256"],
                    )
                    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                    self.assertEqual(
                        set(artifact["assertion_proofs"]),
                        set(adjudication["assertion_results"]),
                    )
                    self.assertTrue(artifact["source_evidence"])
                    self.assertTrue(
                        all(artifact["assertion_proofs"].values()),
                        artifact["assertion_proofs"],
                    )

            serialized = json.dumps(bundle, sort_keys=True).casefold()
            self.assertNotIn("check whether there is a newer", serialized)
            self.assertNotIn("ask a supported permanent-residence", serialized)
            self.assertTrue((output_dir / "automated-adjudications.json").is_file())

    def test_builder_rejects_missing_browser_test_instead_of_inferring_a_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release, browser, provider = self._write_inputs(root)
            browser_payload = json.loads(browser.read_text(encoding="utf-8"))
            browser_payload["tests"] = browser_payload["tests"][:-1]
            browser.write_text(json.dumps(browser_payload), encoding="utf-8")

            with self.assertRaisesRegex(MachineEvidenceError, "eval-019"):
                build_automated_workflow_evidence(
                    repo_root=ROOT,
                    output_dir=root / "evidence",
                    release_monitor_path=release,
                    browser_workflow_report_path=browser,
                    provider_recovery_report_path=provider,
                )

    def test_builder_rejects_missing_browser_observation_instead_of_blanket_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release, browser, provider = self._write_inputs(root)
            browser_payload = json.loads(browser.read_text(encoding="utf-8"))
            browser_payload["tests"][3]["observations"].remove(
                "identity_urls_and_ui_exclude_secrets"
            )
            browser.write_text(json.dumps(browser_payload), encoding="utf-8")

            with self.assertRaisesRegex(
                MachineEvidenceError,
                "browser observations.*eval-019",
            ):
                build_automated_workflow_evidence(
                    repo_root=ROOT,
                    output_dir=root / "evidence",
                    release_monitor_path=release,
                    browser_workflow_report_path=browser,
                    provider_recovery_report_path=provider,
                )

    def test_provider_recovery_execution_uses_app_seam_and_retains_no_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "provider-recovery.json"

            report = run_provider_recovery_execution(output_path=output)

            self.assertEqual(report["exit_status"], 0)
            self.assertEqual(
                report["observations"]["remote_fallback_request_count"], 0
            )
            self.assertTrue(report["observations"]["question_preserved_for_retry"])
            self.assertTrue(report["observations"]["partial_answer_not_saved"])
            serialized = json.dumps(report, sort_keys=True).casefold()
            self.assertNotIn("what danish test", serialized)
            self.assertNotIn("permanent residence", serialized)

    def test_cli_generates_and_applies_machine_bundle_without_claiming_human_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release, browser, provider = self._write_inputs(root)
            output = root / "final-report.json"

            status = main(
                [
                    "--repo-root",
                    str(ROOT),
                    "--output",
                    str(output),
                    "--generate-automated-evidence",
                    str(root / "evidence"),
                    "--release-monitor-report",
                    str(release),
                    "--browser-workflow-report",
                    str(browser),
                    "--provider-recovery-report",
                    str(provider),
                    "--generated-at-utc",
                    "2026-07-14T17:00:00Z",
                ]
            )

            self.assertEqual(status, 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["adjudications"]["automated_workflow_case_count"], 6)
            self.assertEqual(report["adjudications"]["independent_human_case_count"], 0)
            self.assertEqual(report["execution"]["not_evaluable_count"], 0)
            by_id = {item["case_id"]: item for item in report["case_results"]}
            for index in range(10, 14):
                case_id = next(
                    value for value in by_id if value.startswith(f"eval-{index:03d}-")
                )
                self.assertEqual(by_id[case_id]["status"], "passed")
            for index in range(15, 21):
                case_id = next(
                    value for value in by_id if value.startswith(f"eval-{index:03d}-")
                )
                self.assertEqual(by_id[case_id]["status"], "passed")

    def test_final_evaluator_rechecks_nested_source_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release, browser, provider = self._write_inputs(root)
            bundle = build_automated_workflow_evidence(
                repo_root=ROOT,
                output_dir=root / "evidence",
                release_monitor_path=release,
                browser_workflow_report_path=browser,
                provider_recovery_report_path=provider,
            )

            release.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                FinalAnswerEvaluationError,
                "source evidence SHA-256 does not match",
            ):
                generate_final_answer_evaluation(
                    ROOT,
                    runner=_FailingAnswerRunner(),
                    mode="controlled",
                    adjudications=bundle,
                )

    def test_final_evaluator_rejects_fabricated_automated_observation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release, browser, provider = self._write_inputs(root)
            bundle = build_automated_workflow_evidence(
                repo_root=ROOT,
                output_dir=root / "evidence",
                release_monitor_path=release,
                browser_workflow_report_path=browser,
                provider_recovery_report_path=provider,
            )
            adjudication = bundle["cases"][0]
            artifact_path = Path(adjudication["evidence_binding"]["path"])
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            assertion_id = next(iter(artifact["assertion_proofs"]))
            artifact["assertion_proofs"][assertion_id][0][
                "observation_id"
            ] = "fabricated-pass"
            artifact_path.write_text(
                json.dumps(artifact, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            adjudication["evidence_binding"]["sha256"] = hashlib.sha256(
                artifact_path.read_bytes()
            ).hexdigest()

            with self.assertRaisesRegex(
                FinalAnswerEvaluationError,
                "approved automated proof contract",
            ):
                generate_final_answer_evaluation(
                    ROOT,
                    runner=_FailingAnswerRunner(),
                    mode="controlled",
                    adjudications=bundle,
                )

    def test_final_evaluator_revalidates_nested_automated_source_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release, browser, provider = self._write_inputs(root)
            bundle = build_automated_workflow_evidence(
                repo_root=ROOT,
                output_dir=root / "evidence",
                release_monitor_path=release,
                browser_workflow_report_path=browser,
                provider_recovery_report_path=provider,
            )
            release_payload = json.loads(release.read_text(encoding="utf-8"))
            release_payload["strict_passed"] = False
            release.write_text(json.dumps(release_payload), encoding="utf-8")
            release_sha256 = hashlib.sha256(release.read_bytes()).hexdigest()

            adjudication = next(
                item
                for item in bundle["cases"]
                if item["case_id"] == "eval-015-update-telemetry-privacy"
            )
            artifact_path = Path(adjudication["evidence_binding"]["path"])
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            release_source = next(
                item
                for item in artifact["source_evidence"]
                if item["source_id"] == "release-monitor"
            )
            release_source["sha256"] = release_sha256
            artifact_path.write_text(
                json.dumps(artifact, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            adjudication["evidence_binding"]["sha256"] = hashlib.sha256(
                artifact_path.read_bytes()
            ).hexdigest()

            with self.assertRaisesRegex(
                FinalAnswerEvaluationError,
                "not a strict live run",
            ):
                generate_final_answer_evaluation(
                    ROOT,
                    runner=_FailingAnswerRunner(),
                    mode="controlled",
                    adjudications=bundle,
                )

    def test_non_answer_workflows_do_not_self_certify_answer_behavior(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release, browser, provider = self._write_inputs(root)
            bundle = build_automated_workflow_evidence(
                repo_root=ROOT,
                output_dir=root / "evidence",
                release_monitor_path=release,
                browser_workflow_report_path=browser,
                provider_recovery_report_path=provider,
            )
            for adjudication in bundle["cases"]:
                artifact_path = Path(adjudication["evidence_binding"]["path"])
                artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                self.assertNotIn("observed_behavior", artifact)
                self.assertNotIn("behavior_proof", artifact)
                self.assertNotIn("observed_behavior", adjudication)

            report = generate_final_answer_evaluation(
                ROOT,
                runner=_FailingAnswerRunner(),
                mode="controlled",
                adjudications=bundle,
            )
            workflow_results = [
                item
                for item in report["case_results"]
                if item["evaluation_surface"]
                in {
                    "browser-workflow",
                    "knowledge-release-workflow",
                    "provider-recovery-workflow",
                }
            ]
            self.assertEqual(len(workflow_results), 6)
            self.assertTrue(
                all(
                    item["checks"]["behavior"]["status"] == "not_applicable"
                    for item in workflow_results
                )
            )
            self.assertEqual(
                report["metrics"]["clarify_answer_refuse_accuracy"][
                    "applicable_case_count"
                ],
                14,
            )

    def test_workflow_cases_reject_human_methods_that_bypass_machine_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release, browser, provider = self._write_inputs(root)
            bundle = build_automated_workflow_evidence(
                repo_root=ROOT,
                output_dir=root / "evidence",
                release_monitor_path=release,
                browser_workflow_report_path=browser,
                provider_recovery_report_path=provider,
            )
            for method in (
                "manual-workflow-review",
                "independent-human-review",
            ):
                with self.subTest(method=method):
                    changed = json.loads(json.dumps(bundle))
                    changed["cases"][0]["assessment_method"] = method
                    with self.assertRaisesRegex(
                        FinalAnswerEvaluationError,
                        "requires automated-workflow-test",
                    ):
                        generate_final_answer_evaluation(
                            ROOT,
                            runner=_FailingAnswerRunner(),
                            mode="controlled",
                            adjudications=changed,
                        )


if __name__ == "__main__":
    unittest.main()
