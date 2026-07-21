import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from danish_rag.release_evaluation import generate_release_evaluation


ROOT = Path(__file__).resolve().parents[1]


def _gate(report, gate_id):
    return next(gate for gate in report["gate_results"] if gate["id"] == gate_id)


def _copy_release_fixture(target):
    shutil.copytree(ROOT / "config", target / "config")
    (target / "data").mkdir()
    shutil.copytree(ROOT / "data" / "evaluation", target / "data" / "evaluation")
    (target / "docs").mkdir()
    shutil.copytree(ROOT / "docs" / "progress", target / "docs" / "progress")


def _set_live_evidence_gate_statuses(target, status="passed"):
    qualification_path = target / "config" / "release-qualification.json"
    qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
    for gate in qualification["gate_results"]:
        gate_id = gate["id"]
        metric_id = gate.get("metric_id")
        if metric_id == "official-fact-citation-coverage":
            gate["status"] = status
        elif metric_id == "privacy-network-boundary" and "fixture" not in gate_id:
            gate["status"] = status
        elif metric_id == "update-rollback-success" and "fixture" not in gate_id:
            gate["status"] = status
        elif metric_id == "environment-matrix-critical-journeys":
            gate["status"] = status
            published_environment = next(
                environment
                for environment in qualification["supported_environment_matrix"]
                if "release_gate_status" in environment
            )
            published_environment["release_gate_status"] = status
            published_environment["status"] = (
                "live-critical-journeys-passed"
                if status == "passed"
                else "replacement-live-evidence-required"
            )
    qualification_path.write_text(json.dumps(qualification), encoding="utf-8")


def _write_live_release_evidence(target):
    progress = target / "docs" / "progress"
    quality_bar_path = target / "config" / "evaluation-quality-bar.json"
    quality_bar = json.loads(quality_bar_path.read_text(encoding="utf-8"))
    runtime_policy = json.loads(
        (target / "config" / "runtime-policy.json").read_text(encoding="utf-8")
    )
    dataset_path = target / quality_bar["evaluation_set"]["path"]
    case_count = quality_bar["evaluation_set"]["case_count"]
    final_answer_path = progress / "final-answer-evaluation-live.json"
    final_answer_path.write_text(
        json.dumps(
            {
                "schema_version": "final-answer-evaluation-v1",
                "generated_at_utc": "2026-07-14T12:00:00Z",
                "dataset": {
                    "dataset_id": quality_bar["evaluation_set"]["dataset_id"],
                    "version": quality_bar["evaluation_set"]["version"],
                    "case_count": case_count,
                    "sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
                },
                "quality_bar": {
                    "quality_bar_id": quality_bar["quality_bar_id"],
                    "version": quality_bar["version"],
                    "sha256": hashlib.sha256(
                        quality_bar_path.read_bytes()
                    ).hexdigest(),
                },
                "execution": {
                    "mode": "live-ollama",
                    "case_count": case_count,
                    "completed_count": case_count,
                    "not_evaluable_count": 0,
                    "error_count": 0,
                    "live_provider_calls": True,
                },
                "case_results": [
                    {"case_id": f"case-{case_number}"}
                    for case_number in range(1, case_count + 1)
                ],
                "metrics": {
                    metric_id: {"status": "passed"}
                    for metric_id in (
                        "required_fact_coverage",
                        "forbidden_claims",
                        "privacy_requirement_compliance",
                        "official_fact_citation_coverage",
                        "citation_correctness",
                        "unsupported_claim_rate",
                        "personal_eligibility_conclusions",
                        "clarify_answer_refuse_accuracy",
                        "trust_indicator_correctness",
                        "fresh_tomato_min_material_source_rule_pass_rate",
                        "required_source_domain_coverage",
                        "forbidden_source_domain_violations",
                        "case_execution_errors",
                        "evaluation_surface_completion",
                    )
                },
                "threshold_failures": [],
                "strict_passed": True,
            }
        ),
        encoding="utf-8",
    )

    rollback_phases = [
        "verification",
        "extraction",
        "embedding",
        "indexing",
        "activation",
        "late_activation",
    ]
    journeys = [
        "setup",
        "supported-answer",
        "refusal",
        "evidence-inspection",
        "history-persistence",
        "deletion-export",
        "update-installation",
        "rollback",
    ]
    monitor_path = progress / "release-monitors-live.json"
    monitor_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": "2026-07-14T12:00:00Z",
                "mode": "live",
                "privacy": {
                    "monitor_id": "release-network-boundary-monitor",
                    "mode": "live",
                    "observed_workflows": [
                        "question",
                        "retrieval",
                        "generation",
                        "evidence_inspection",
                        "history",
                        "deletion",
                        "export",
                        "local_indexing",
                        "knowledge_update_review",
                    ],
                    "forbidden_request_count": 0,
                    "release_request_inspection": {"content_free": True},
                    "failures": [],
                    "passed": True,
                },
                "rollback": {
                    "monitor_id": "knowledge-release-rollback-fault-matrix",
                    "mode": "live",
                    "results": [
                        {
                            "phase": phase,
                            "status": "passed",
                            "signature_verification_passed": True,
                            "signature_rejection_observed": True,
                            "fault_injected": True,
                            "failure_observed": True,
                            "prior_pair_unchanged": True,
                            "prior_pair_queryable": True,
                            "target_release_active": False,
                            "installation_reported_success": False,
                        }
                        for phase in rollback_phases
                    ],
                    "failures": [],
                    "passed": True,
                },
                "supported_environment": {
                    "monitor_id": "supported-environment-critical-journeys",
                    "mode": "live",
                    "qualification_scope": "live-supported-environment",
                    "can_qualify_supported_environment": True,
                    "live_provider_calls": True,
                    "execution_evidence": {
                        "transport": "loopback-bound-process",
                        "browser_driver": "playwright",
                        "browser_phase_count": 2,
                        "app_process_start_count": 2,
                        "app_process_stop_count": 2,
                        "history_restart_observed": True,
                        "browser_evidence_available": True,
                    },
                    "observed_environment_identity": {
                        "host_os": "Windows",
                        "windows_version": "11",
                        "windows_build": "26100",
                        "wsl_version": "2",
                        "distribution_id": "ubuntu",
                        "distribution_version": "24.04",
                        "architecture": "x86_64",
                        "python_version": "3.12.3",
                        "ollama_version": "0.30.6",
                        "browser_name": "chromium",
                        "browser_version": "150.0.7871.114",
                    },
                    "environment_identity_validation": {
                        "passed": True,
                        "checks": {
                            "windows_11": True,
                            "wsl2_ubuntu": True,
                            "architecture": True,
                            "python": True,
                            "ollama": True,
                            "browser": True,
                            "policy_contract": True,
                        },
                    },
                    "supported_environment_identity": runtime_policy[
                        "supported_environment"
                    ]["first_verified"],
                    "provider_identity": {
                        "provider_id": "ollama",
                        "provider_version": "0.30.6",
                    },
                    "model_identity": {
                        "generation_model": "gemma4:12b",
                        "embedding_model": "embeddinggemma",
                    },
                    "corpus_identity": {
                        "knowledge_release_id": "kr-2026-07-06.1",
                        "corpus_id": "kr-2026-07-06.1",
                        "source_registry_version": "sr-2026-07-06.1",
                        "embedding_model": "embeddinggemma",
                        "embedding_vector_dimensions": 768,
                        "index_schema_version": "hybrid-index-v1",
                    },
                    "journeys": [
                        {"id": journey, "status": "passed"} for journey in journeys
                    ],
                    "failures": [],
                    "passed": True,
                },
                "component_passed": True,
                "strict_passed": True,
            }
        ),
        encoding="utf-8",
    )
    return final_answer_path, monitor_path


class ReleaseEvaluationReportTests(unittest.TestCase):
    def test_report_covers_current_release_gates_and_keeps_release_blocked(self):
        report = generate_release_evaluation(
            ROOT,
            generated_at_utc="2026-07-07T00:00:00Z",
        )
        qualification = json.loads(
            (ROOT / "config" / "release-qualification.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(report["schema_version"], "1.0")
        self.assertEqual(report["generated_at_utc"], "2026-07-07T00:00:00Z")
        self.assertEqual(
            report["release_qualification_id"],
            qualification["qualification_id"],
        )
        self.assertEqual(report["qualification_status"], "blocked")
        self.assertEqual(report["release_decision"], "do-not-release")
        self.assertFalse(report["strict_release_passed"])

        configured_gate_ids = {gate["id"] for gate in qualification["gate_results"]}
        reported_gate_ids = {gate["id"] for gate in report["gate_results"]}
        self.assertEqual(reported_gate_ids, configured_gate_ids)

        blocker_ids = {blocker["id"] for blocker in report["derived_release_blockers"]}
        self.assertNotIn("quality-bar-human-approval-pending", blocker_ids)
        self.assertNotIn("issue-24-human-validation-pending", blocker_ids)
        self.assertIn("supported-environment-critical-journeys", blocker_ids)
        self.assertIn("final-answer-independent-human-adjudication", blocker_ids)
        self.assertIn("production-source-registry-qualification", blocker_ids)
        self.assertIn("browser-accessibility-suite", blocker_ids)
        self.assertNotIn("retrieval-required-evidence-baseline", blocker_ids)

        accessibility_gate = _gate(report, "browser-accessibility-suite")
        self.assertEqual(accessibility_gate["status"], "not_verified")
        self.assertIn(
            "manual assistive-technology",
            accessibility_gate["summary"],
        )
        self.assertIn("has not been rerun", accessibility_gate["summary"])

    def test_retrieval_gate_uses_hybrid_evidence_and_quality_bar_thresholds(self):
        report = generate_release_evaluation(
            ROOT,
            generated_at_utc="2026-07-07T00:00:00Z",
        )

        gate = _gate(report, "retrieval-required-evidence-baseline")

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["evaluated_status"], "passed")
        self.assertEqual(gate["observed"]["required_evidence_recall_at_3"], 1.0)
        self.assertEqual(gate["observed"]["required_evidence_query_count"], 7)
        self.assertEqual(gate["observed"]["blocked_source_violations"], 0)
        self.assertEqual(gate["observed"]["forbidden_result_violations"], 0)
        self.assertEqual(gate["thresholds"]["required_evidence_recall_at_3_min"], 0.95)
        self.assertEqual(gate["thresholds"]["blocked_source_violations_max"], 0)
        self.assertEqual(gate["thresholds"]["forbidden_result_violations_max"], 0)
        self.assertEqual(gate["failures"], [])

    def test_live_reports_evaluate_final_answer_and_release_monitor_gates_by_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            _set_live_evidence_gate_statuses(fixture_root)
            final_answer_path, monitor_path = _write_live_release_evidence(
                fixture_root
            )

            report = generate_release_evaluation(
                fixture_root,
                generated_at_utc="2026-07-14T12:30:00Z",
            )

            expected_gate_ids = {
                "final-answer-independent-human-adjudication",
                "release-network-boundary-monitor",
                "knowledge-release-rollback-fault-matrix",
                "supported-environment-critical-journeys",
            }
            for gate_id in expected_gate_ids:
                with self.subTest(gate_id=gate_id):
                    gate = _gate(report, gate_id)
                    self.assertEqual(gate["evaluated_status"], "passed")
                    self.assertEqual(gate["status"], "passed")
                    self.assertEqual(gate["failures"], [])
                    self.assertEqual(len(gate["evidence"]), 1)
                    self.assertEqual(len(gate["evidence"][0]["sha256"]), 64)

            self.assertEqual(
                report["evidence_inputs"]["final_answer_evaluation"]["sha256"],
                hashlib.sha256(final_answer_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                report["evidence_inputs"]["release_monitors"]["sha256"],
                hashlib.sha256(monitor_path.read_bytes()).hexdigest(),
            )

    def test_missing_live_reports_leave_all_live_evidence_gates_not_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            _set_live_evidence_gate_statuses(fixture_root)
            for filename in (
                "final-answer-evaluation-live.json",
                "release-monitors-live.json",
            ):
                (fixture_root / "docs" / "progress" / filename).unlink(
                    missing_ok=True
                )

            report = generate_release_evaluation(fixture_root)

            for gate_id in (
                "final-answer-independent-human-adjudication",
                "release-network-boundary-monitor",
                "knowledge-release-rollback-fault-matrix",
                "supported-environment-critical-journeys",
            ):
                with self.subTest(gate_id=gate_id):
                    gate = _gate(report, gate_id)
                    self.assertEqual(gate["evaluated_status"], "not_run")
                    self.assertEqual(gate["status"], "not_run")
                    self.assertFalse(gate["evidence"][0]["exists"])
                    self.assertTrue(gate["failures"])

    def test_malformed_live_reports_fail_closed_without_aborting_other_gates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            _set_live_evidence_gate_statuses(fixture_root)
            progress = fixture_root / "docs" / "progress"
            for filename in (
                "final-answer-evaluation-live.json",
                "release-monitors-live.json",
            ):
                (progress / filename).write_text("{not json", encoding="utf-8")

            report = generate_release_evaluation(fixture_root)

            for gate_id in (
                "final-answer-independent-human-adjudication",
                "release-network-boundary-monitor",
                "knowledge-release-rollback-fault-matrix",
                "supported-environment-critical-journeys",
            ):
                with self.subTest(gate_id=gate_id):
                    gate = _gate(report, gate_id)
                    self.assertEqual(gate["evaluated_status"], "failed")
                    self.assertEqual(gate["status"], "failed")
                    self.assertTrue(gate["evidence"][0]["exists"])
                    self.assertEqual(len(gate["evidence"][0]["sha256"]), 64)
                    self.assertTrue(
                        any(
                            "malformed or ambiguous JSON" in failure
                            for failure in gate["failures"]
                        ),
                        gate["failures"],
                    )

    def test_non_live_or_non_strict_reports_never_pass_live_evidence_gates(self):
        scenarios = ("non-live", "non-strict")
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmpdir:
                fixture_root = Path(tmpdir)
                _copy_release_fixture(fixture_root)
                _set_live_evidence_gate_statuses(fixture_root)
                final_answer_path, monitor_path = _write_live_release_evidence(
                    fixture_root
                )
                final_answer = json.loads(
                    final_answer_path.read_text(encoding="utf-8")
                )
                monitors = json.loads(monitor_path.read_text(encoding="utf-8"))
                if scenario == "non-live":
                    final_answer["execution"]["mode"] = "controlled"
                    final_answer["execution"]["live_provider_calls"] = False
                    monitors["mode"] = "fixture-non-live"
                    monitors["privacy"]["mode"] = "fixture-non-live"
                    monitors["rollback"]["mode"] = "fixture-non-live"
                    monitors["supported_environment"]["mode"] = "fixture-non-live"
                    monitors["supported_environment"][
                        "qualification_scope"
                    ] = "non-live-fixture-only"
                    monitors["supported_environment"][
                        "can_qualify_supported_environment"
                    ] = False
                    monitors["supported_environment"]["live_provider_calls"] = False
                else:
                    final_answer["strict_passed"] = False
                    monitors["strict_passed"] = False
                final_answer_path.write_text(
                    json.dumps(final_answer), encoding="utf-8"
                )
                monitor_path.write_text(json.dumps(monitors), encoding="utf-8")

                report = generate_release_evaluation(fixture_root)

                for gate_id in (
                    "final-answer-independent-human-adjudication",
                    "release-network-boundary-monitor",
                    "knowledge-release-rollback-fault-matrix",
                    "supported-environment-critical-journeys",
                ):
                    gate = _gate(report, gate_id)
                    self.assertEqual(gate["evaluated_status"], "failed")
                    self.assertEqual(gate["status"], "failed")
                    self.assertTrue(gate["failures"])

    def test_live_evidence_evaluation_survives_release_gate_id_updates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            qualification_path = fixture_root / "config" / "release-qualification.json"
            qualification = json.loads(
                qualification_path.read_text(encoding="utf-8")
            )
            updated_ids = {
                "final-answer-independent-human-adjudication": "renamed-final-answer-gate",
                "release-network-boundary-monitor": "renamed-privacy-gate",
                "knowledge-release-rollback-fault-matrix": "renamed-rollback-gate",
                "supported-environment-critical-journeys": "renamed-environment-gate",
            }
            for gate in qualification["gate_results"]:
                if gate["id"] in updated_ids:
                    gate["id"] = updated_ids[gate["id"]]
                    gate["status"] = "passed"
            published_environment = next(
                environment
                for environment in qualification["supported_environment_matrix"]
                if "release_gate_status" in environment
            )
            published_environment["release_gate_status"] = "passed"
            published_environment["status"] = "live-critical-journeys-passed"
            qualification_path.write_text(
                json.dumps(qualification), encoding="utf-8"
            )
            _write_live_release_evidence(fixture_root)

            report = generate_release_evaluation(fixture_root)

            for gate_id in updated_ids.values():
                with self.subTest(gate_id=gate_id):
                    gate = _gate(report, gate_id)
                    self.assertEqual(gate["evaluated_status"], "passed")
                    self.assertEqual(gate["status"], "passed")

    def test_monitor_component_claims_are_cross_checked_before_gates_pass(self):
        scenarios = (
            (
                "release-network-boundary-monitor",
                lambda report: report["privacy"].update(
                    {"forbidden_request_count": 1}
                ),
            ),
            (
                "knowledge-release-rollback-fault-matrix",
                lambda report: report["rollback"]["results"][0].update(
                    {"prior_pair_unchanged": False}
                ),
            ),
            (
                "supported-environment-critical-journeys",
                lambda report: report["supported_environment"]["journeys"][0].update(
                    {"status": "failed"}
                ),
            ),
            (
                "supported-environment-critical-journeys",
                lambda report: report["supported_environment"][
                    "execution_evidence"
                ].update({"history_restart_observed": False}),
            ),
            (
                "supported-environment-critical-journeys",
                lambda report: report["supported_environment"][
                    "observed_environment_identity"
                ].update({"browser_version": "149.0.0"}),
            ),
        )
        for gate_id, mutate in scenarios:
            with self.subTest(gate_id=gate_id), tempfile.TemporaryDirectory() as tmpdir:
                fixture_root = Path(tmpdir)
                _copy_release_fixture(fixture_root)
                _set_live_evidence_gate_statuses(fixture_root)
                _, monitor_path = _write_live_release_evidence(fixture_root)
                monitors = json.loads(monitor_path.read_text(encoding="utf-8"))
                mutate(monitors)
                monitor_path.write_text(json.dumps(monitors), encoding="utf-8")

                report = generate_release_evaluation(fixture_root)

                gate = _gate(report, gate_id)
                self.assertEqual(gate["evaluated_status"], "failed")
                self.assertEqual(gate["status"], "failed")
                self.assertTrue(gate["failures"])

    def test_failed_environment_component_does_not_erase_passing_monitor_components(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            _set_live_evidence_gate_statuses(fixture_root)
            _, monitor_path = _write_live_release_evidence(fixture_root)
            monitor = json.loads(monitor_path.read_text(encoding="utf-8"))
            environment = monitor["supported_environment"]
            environment["journeys"][0]["status"] = "failed"
            environment["failures"] = ["setup"]
            environment["passed"] = False
            environment["can_qualify_supported_environment"] = False
            monitor["component_passed"] = False
            monitor["strict_passed"] = False
            monitor_path.write_text(json.dumps(monitor), encoding="utf-8")

            report = generate_release_evaluation(fixture_root)

            self.assertEqual(
                _gate(report, "release-network-boundary-monitor")["evaluated_status"],
                "passed",
            )
            self.assertEqual(
                _gate(report, "knowledge-release-rollback-fault-matrix")[
                    "evaluated_status"
                ],
                "passed",
            )
            self.assertEqual(
                _gate(report, "supported-environment-critical-journeys")[
                    "evaluated_status"
                ],
                "failed",
            )

    def test_invalid_live_evidence_content_is_not_copied_into_offline_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            _set_live_evidence_gate_statuses(fixture_root)
            final_answer_path, monitor_path = _write_live_release_evidence(
                fixture_root
            )
            marker = "private-personal-content-marker"
            final_answer = json.loads(final_answer_path.read_text(encoding="utf-8"))
            final_answer["execution"]["mode"] = marker
            final_answer_path.write_text(json.dumps(final_answer), encoding="utf-8")
            monitors = json.loads(monitor_path.read_text(encoding="utf-8"))
            monitors["privacy"]["observed_workflows"].append(marker)
            monitors["supported_environment"]["provider_identity"][marker] = marker
            monitor_path.write_text(json.dumps(monitors), encoding="utf-8")

            report = generate_release_evaluation(fixture_root)

            self.assertNotIn(marker, json.dumps(report, sort_keys=True))

    def test_final_answer_report_must_match_current_dataset_and_quality_bar_hashes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            _set_live_evidence_gate_statuses(fixture_root)
            final_answer_path, _ = _write_live_release_evidence(fixture_root)
            final_answer = json.loads(final_answer_path.read_text(encoding="utf-8"))
            final_answer["dataset"]["sha256"] = "0" * 64
            final_answer["quality_bar"]["sha256"] = "f" * 64
            final_answer_path.write_text(json.dumps(final_answer), encoding="utf-8")

            report = generate_release_evaluation(fixture_root)

            gate = _gate(report, "final-answer-independent-human-adjudication")
            self.assertEqual(gate["evaluated_status"], "failed")
            self.assertEqual(gate["status"], "failed")
            self.assertTrue(
                any("current dataset" in failure for failure in gate["failures"])
            )
            self.assertTrue(
                any("current quality bar" in failure for failure in gate["failures"])
            )

    def test_report_records_privacy_assertions_without_user_content_fields(self):
        report = generate_release_evaluation(
            ROOT,
            generated_at_utc="2026-07-07T00:00:00Z",
        )

        self.assertEqual(
            report["privacy_assertions"],
            {
                "uses_production_user_questions": False,
                "uses_production_answers": False,
                "uses_conversation_identifiers": False,
                "ran_live_network_or_provider_calls": False,
            },
        )

        serialized = json.dumps(report, sort_keys=True).casefold()
        forbidden_fragments = [
            '"production_question_text"',
            '"production_answer_text"',
            '"user_question_text"',
            '"user_answer_text"',
            '"conversation_id"',
            '"conversation_record"',
        ]
        for fragment in forbidden_fragments:
            self.assertNotIn(fragment, serialized)

    def test_source_contract_drift_prevents_strict_release_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            _write_live_release_evidence(fixture_root)
            qualification_path = fixture_root / "config" / "release-qualification.json"
            qualification = json.loads(
                qualification_path.read_text(encoding="utf-8")
            )
            qualification["qualification_status"] = "qualified"
            qualification["release_decision"] = "release"
            qualification["runtime"]["generation_model"] = "drifted-model"
            for gate in qualification["gate_results"]:
                gate["status"] = "passed"
                if gate["id"] == "browser-accessibility-suite":
                    gate["observed"] = {
                        "manual_assistive_technology_check": "passed"
                    }
                    gate["manual_assistive_technology_evidence"] = {
                        "path": "docs/progress/manual-at.json",
                        "sha256": "a" * 64,
                        "reviewer_id": "reviewer",
                        "assistive_technology": "screen-reader version",
                        "browser": "browser version",
                        "tested_at_utc": "2026-07-14T12:00:00Z",
                    }
            published_environment = next(
                environment
                for environment in qualification["supported_environment_matrix"]
                if "release_gate_status" in environment
            )
            published_environment["release_gate_status"] = "passed"
            published_environment["status"] = "live-critical-journeys-passed"
            for approval in qualification["human_approval_records"]:
                approval["status"] = "approved"
            qualification_path.write_text(
                json.dumps(qualification),
                encoding="utf-8",
            )

            report = generate_release_evaluation(
                fixture_root,
                generated_at_utc="2026-07-14T12:30:00Z",
            )

            self.assertFalse(report["strict_release_passed"])
            self.assertTrue(
                any(
                    "generation model" in failure
                    for failure in report["config_validation"]["source_contract"]
                ),
                report["config_validation"],
            )

    def test_accessibility_gate_verifies_hash_bound_manual_journeys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            evidence_path = (
                fixture_root / "docs" / "progress" / "manual-at-evidence.json"
            )
            evidence_payload = {
                "schema_version": "manual-assistive-technology-v1",
                "status": "passed",
                "reviewer_id": "independent-reviewer",
                "assistive_technology": "screen-reader 1.0",
                "browser": "browser 1.0",
                "tested_at_utc": "2026-07-14T12:00:00Z",
                "journeys": [
                    {"id": journey_id, "status": "passed"}
                    for journey_id in (
                        "provider-setup",
                        "question-submission",
                        "answer-status-announcements",
                        "inline-citation-navigation",
                        "evidence-drawer-focus-close",
                        "history-navigation",
                        "update-review",
                        "error-recovery",
                    )
                ],
            }
            evidence_path.write_text(
                json.dumps(evidence_payload),
                encoding="utf-8",
            )
            qualification_path = fixture_root / "config" / "release-qualification.json"
            qualification = json.loads(
                qualification_path.read_text(encoding="utf-8")
            )
            gate = next(
                item
                for item in qualification["gate_results"]
                if item["id"] == "browser-accessibility-suite"
            )
            gate["status"] = "passed"
            gate["observed"]["automated_suite_status"] = "current"
            gate["observed"]["manual_assistive_technology_check"] = "passed"
            gate["manual_assistive_technology_evidence"] = {
                "path": "docs/progress/manual-at-evidence.json",
                "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                "reviewer_id": evidence_payload["reviewer_id"],
                "assistive_technology": evidence_payload["assistive_technology"],
                "browser": evidence_payload["browser"],
                "tested_at_utc": evidence_payload["tested_at_utc"],
            }
            qualification_path.write_text(json.dumps(qualification), encoding="utf-8")

            passed_report = generate_release_evaluation(fixture_root)
            passed_gate = _gate(passed_report, "browser-accessibility-suite")

            self.assertEqual(passed_gate["evaluated_status"], "passed")
            self.assertEqual(passed_gate["status"], "passed")

            evidence_payload["journeys"].extend(
                [
                    {"id": "provider-setup", "status": "passed"},
                    {"id": "unpublished-extra-journey", "status": "passed"},
                ]
            )
            evidence_path.write_text(json.dumps(evidence_payload), encoding="utf-8")
            gate["manual_assistive_technology_evidence"]["sha256"] = (
                hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            )
            qualification_path.write_text(json.dumps(qualification), encoding="utf-8")
            invalid_journeys_report = generate_release_evaluation(fixture_root)
            invalid_journeys_gate = _gate(
                invalid_journeys_report, "browser-accessibility-suite"
            )
            self.assertEqual(invalid_journeys_gate["status"], "failed")
            self.assertTrue(
                any(
                    "duplicated" in failure
                    for failure in invalid_journeys_gate["failures"]
                ),
                invalid_journeys_gate["failures"],
            )
            self.assertTrue(
                any(
                    "unknown" in failure
                    for failure in invalid_journeys_gate["failures"]
                ),
                invalid_journeys_gate["failures"],
            )

            ambiguous_payload = json.dumps(evidence_payload).replace(
                '"status": "passed"',
                '"status": "failed", "status": "passed"',
                1,
            )
            evidence_path.write_text(ambiguous_payload, encoding="utf-8")
            gate["manual_assistive_technology_evidence"]["sha256"] = (
                hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            )
            qualification_path.write_text(json.dumps(qualification), encoding="utf-8")
            ambiguous_report = generate_release_evaluation(fixture_root)
            ambiguous_gate = _gate(ambiguous_report, "browser-accessibility-suite")
            self.assertEqual(ambiguous_gate["status"], "failed")
            self.assertTrue(
                any(
                    "malformed or ambiguous" in failure
                    for failure in ambiguous_gate["failures"]
                ),
                ambiguous_gate["failures"],
            )

            evidence_payload["tested_at_utc"] = "yesterday"
            gate["manual_assistive_technology_evidence"]["tested_at_utc"] = (
                "yesterday"
            )
            evidence_path.write_text(json.dumps(evidence_payload), encoding="utf-8")
            gate["manual_assistive_technology_evidence"]["sha256"] = (
                hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            )
            qualification_path.write_text(json.dumps(qualification), encoding="utf-8")
            invalid_timestamp_report = generate_release_evaluation(fixture_root)
            invalid_timestamp_gate = _gate(
                invalid_timestamp_report, "browser-accessibility-suite"
            )
            self.assertEqual(invalid_timestamp_gate["status"], "failed")
            self.assertTrue(
                any(
                    "UTC second precision" in failure
                    for failure in invalid_timestamp_gate["failures"]
                ),
                invalid_timestamp_gate["failures"],
            )

            evidence_payload["journeys"][0]["status"] = "failed"
            evidence_path.write_text(json.dumps(evidence_payload), encoding="utf-8")
            failed_report = generate_release_evaluation(fixture_root)
            failed_gate = _gate(failed_report, "browser-accessibility-suite")
            self.assertEqual(failed_gate["status"], "failed")
            self.assertTrue(
                any("hash does not match" in failure for failure in failed_gate["failures"]),
                failed_gate["failures"],
            )

    def test_default_output_path_is_not_written_by_core_generator(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "release-evaluation-current.json"

            report = generate_release_evaluation(
                ROOT,
                generated_at_utc="2026-07-07T00:00:00Z",
            )

            self.assertFalse(output_path.exists())
            json.dumps(report, sort_keys=True)

    def test_missing_retrieval_evidence_marks_retrieval_gate_not_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            (
                fixture_root
                / "docs"
                / "progress"
                / "issue-29-hybrid-retrieval-comparison.json"
            ).unlink()

            report = generate_release_evaluation(
                fixture_root,
                generated_at_utc="2026-07-07T00:00:00Z",
            )

            gate = _gate(report, "retrieval-required-evidence-baseline")
            self.assertEqual(gate["status"], "not_run")
            self.assertEqual(gate["evaluated_status"], "not_run")
            self.assertTrue(
                any("missing evidence file" in failure for failure in gate["failures"]),
                gate["failures"],
            )

    def test_malformed_retrieval_evidence_raises_runner_error(self):
        from danish_rag.release_evaluation import ReleaseEvaluationError

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            (
                fixture_root
                / "docs"
                / "progress"
                / "issue-29-hybrid-retrieval-comparison.json"
            ).write_text("{not json", encoding="utf-8")

            with self.assertRaises(ReleaseEvaluationError):
                generate_release_evaluation(
                    fixture_root,
                    generated_at_utc="2026-07-07T00:00:00Z",
                )

    def test_weakened_retrieval_evidence_fails_gate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            evidence_path = (
                fixture_root
                / "docs"
                / "progress"
                / "issue-29-hybrid-retrieval-comparison.json"
            )
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["candidates"]["hybrid"]["summary"]["recall_at_3"] = 0.5
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

            report = generate_release_evaluation(
                fixture_root,
                generated_at_utc="2026-07-07T00:00:00Z",
            )

            gate = _gate(report, "retrieval-required-evidence-baseline")
            self.assertEqual(gate["status"], "failed")
            self.assertTrue(
                any("below" in failure for failure in gate["failures"]),
                gate["failures"],
            )

    def test_cli_writes_report_and_strict_mode_fails_for_blocked_release(self):
        from danish_rag.release_evaluation import main

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "release-evaluation-current.json"

            default_status = main(
                [
                    "--repo-root",
                    str(ROOT),
                    "--output",
                    str(output_path),
                    "--generated-at-utc",
                    "2026-07-07T00:00:00Z",
                ]
            )
            strict_status = main(
                [
                    "--repo-root",
                    str(ROOT),
                    "--output",
                    str(output_path),
                    "--strict",
                    "--generated-at-utc",
                    "2026-07-07T00:00:00Z",
                ]
            )

            self.assertEqual(default_status, 0)
            self.assertEqual(strict_status, 1)
            self.assertTrue(output_path.exists())
            written_report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(written_report["generated_at_utc"], "2026-07-07T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
