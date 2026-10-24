from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import danish_rag.release_monitors as release_monitors
from danish_rag.knowledge_update_controller import (
    KnowledgeUpdateController,
    KnowledgeUpdateUnavailable,
)
from danish_rag.release_monitors import (
    main,
    run_network_boundary_monitor,
    run_rollback_fault_matrix,
    run_supported_environment_critical_journeys,
)
from tests.evaluation_time import fixed_clock


class ReleaseNetworkBoundaryMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixture_monitor_observes_every_local_workflow_without_retaining_content(self):
        evidence = await run_network_boundary_monitor(mode="fixture", clock=fixed_clock)

        self.assertEqual(evidence["monitor_id"], "release-network-boundary-monitor")
        self.assertEqual(evidence["mode"], "fixture-non-live")
        self.assertEqual(
            evidence["observed_workflows"],
            [
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
        )
        self.assertEqual(evidence["forbidden_request_count"], 0)
        github_requests = [
            request
            for request in evidence["network_requests"]
            if request["operation"]
            in {
                "knowledge_release_discovery",
                "approved_knowledge_release_artifact_retrieval",
            }
        ]
        self.assertEqual(
            [request["operation"] for request in github_requests],
            [
                "knowledge_release_discovery",
                "approved_knowledge_release_artifact_retrieval",
            ],
        )
        self.assertTrue(all(request["allowed"] for request in github_requests))
        self.assertFalse(github_requests[0]["release_approved"])
        self.assertTrue(github_requests[1]["release_approved"])
        self.assertEqual(github_requests[0]["host"], "api.github.com")
        self.assertEqual(github_requests[1]["host"], "github.com")
        self.assertTrue(all(request["field_names"] == [] for request in github_requests))
        self.assertTrue(
            evidence["release_request_inspection"]["actual_github_transport_observed"]
        )
        self.assertTrue(
            evidence["release_request_inspection"][
                "unapproved_artifact_transport_blocked"
            ]
        )
        self.assertEqual(
            evidence["release_request_inspection"]["actual_operations"],
            [
                "knowledge_release_discovery",
                "approved_knowledge_release_artifact_retrieval",
            ],
        )
        self.assertTrue(evidence["release_request_inspection"]["content_free"])
        self.assertEqual(
            evidence["release_request_inspection"]["field_names"],
            [
                "active_knowledge_release_id",
                "application_version",
                "operation",
            ],
        )
        serialized = json.dumps(evidence, sort_keys=True).casefold()
        self.assertNotIn("what danish test", serialized)
        self.assertNotIn("permanent residence?", serialized)
        self.assertNotIn("supported by", serialized)
        self.assertTrue(evidence["passed"])


class UpdateEvidenceUsesControllerTests(unittest.IsolatedAsyncioTestCase):
    """Update and install evidence comes from the Knowledge update controller (#80)."""

    async def test_network_monitor_update_review_calls_controller_not_http(self):
        requested_paths: list[str] = []
        original_send = httpx.AsyncClient.send

        async def recording_send(client, request, **kwargs):
            requested_paths.append(request.url.path)
            return await original_send(client, request, **kwargs)

        direct_checks: list[bool] = []
        original_check = KnowledgeUpdateController.manual_check

        def recording_check(controller, *args, **kwargs):
            direct_checks.append(True)
            return original_check(controller, *args, **kwargs)

        with patch.object(httpx.AsyncClient, "send", recording_send), patch.object(
            KnowledgeUpdateController, "manual_check", recording_check
        ):
            evidence = await run_network_boundary_monitor(mode="fixture", clock=fixed_clock)

        self.assertTrue(evidence["passed"])
        self.assertIn("/ask", requested_paths)
        self.assertNotIn("/knowledge-updates/check", requested_paths)
        self.assertEqual(len(direct_checks), 1)

    def test_rollback_matrix_installs_through_controller(self):
        started: list[str] = []
        original_start = KnowledgeUpdateController.start_install

        def recording_start(controller, release_id):
            started.append(release_id)
            return original_start(controller, release_id)

        with patch.object(KnowledgeUpdateController, "start_install", recording_start):
            evidence = run_rollback_fault_matrix(mode="fixture", clock=fixed_clock)

        self.assertTrue(evidence["passed"])
        self.assertEqual(len(started), 6)

    def test_install_that_outlives_the_timeout_fails_the_phase_explicitly(self):
        release = threading.Event()

        def blocked_install(*args, **kwargs):
            release.wait(10)

        try:
            with patch(
                "danish_rag.knowledge_update_controller.install_knowledge_release",
                blocked_install,
            ):
                evidence = run_rollback_fault_matrix(
                    mode="fixture",
                    clock=fixed_clock,
                    install_timeout_seconds=0.2,
                )
        finally:
            release.set()

        self.assertFalse(evidence["passed"])
        for result in evidence["results"]:
            with self.subTest(phase=result["phase"]):
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["failure_type"], "InstallTimeout")
                self.assertFalse(result["failure_observed"])

    def test_discovery_failure_is_a_setup_error_not_an_observed_install_failure(self):
        with patch.object(
            KnowledgeUpdateController,
            "manual_check",
            side_effect=KnowledgeUpdateUnavailable("discovery broke"),
        ):
            evidence = run_rollback_fault_matrix(mode="fixture", clock=fixed_clock)

        self.assertFalse(evidence["passed"])
        for result in evidence["results"]:
            with self.subTest(phase=result["phase"]):
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["failure_observed"])
                self.assertEqual(
                    result["failure_type"], "SetupError:KnowledgeUpdateUnavailable"
                )

    def test_signature_is_tampered_after_discovery_and_rejected_at_install(self):
        seen: dict[str, list[str]] = {"check": [], "install": []}
        original_check = KnowledgeUpdateController.manual_check
        original_start = KnowledgeUpdateController.start_install

        def signature_digest(controller, release_id):
            path = controller._catalog_dir / release_id / "manifest.sig"
            return hashlib.sha256(path.read_bytes()).hexdigest()

        def recording_check(controller, *args, **kwargs):
            release_id = next(p.name for p in controller._catalog_dir.iterdir())
            seen["check"].append(signature_digest(controller, release_id))
            return original_check(controller, *args, **kwargs)

        def recording_start(controller, release_id):
            seen["install"].append(signature_digest(controller, release_id))
            return original_start(controller, release_id)

        with patch.object(
            KnowledgeUpdateController, "manual_check", recording_check
        ), patch.object(KnowledgeUpdateController, "start_install", recording_start):
            evidence = run_rollback_fault_matrix(mode="fixture", clock=fixed_clock)

        verification = evidence["results"][0]
        self.assertEqual(verification["phase"], "verification")
        self.assertEqual(verification["failure_type"], "KnowledgeReleaseError")
        self.assertTrue(verification["signature_rejection_observed"])
        self.assertNotEqual(seen["check"][0], seen["install"][0])


class ReleaseRollbackFaultMatrixTests(unittest.TestCase):
    def test_fixture_matrix_proves_every_failed_phase_keeps_prior_pair_queryable(self):
        evidence = run_rollback_fault_matrix(mode="fixture", clock=fixed_clock)

        self.assertEqual(evidence["monitor_id"], "knowledge-release-rollback-fault-matrix")
        self.assertEqual(evidence["mode"], "fixture-non-live")
        self.assertEqual(
            [result["phase"] for result in evidence["results"]],
            [
                "verification",
                "extraction",
                "embedding",
                "indexing",
                "activation",
                "late_activation",
            ],
        )
        for result in evidence["results"]:
            with self.subTest(phase=result["phase"]):
                self.assertEqual(result["status"], "passed")
                self.assertTrue(result["failure_observed"])
                self.assertEqual(
                    result["failure_type"],
                    "KnowledgeReleaseError"
                    if result["phase"] == "verification"
                    else "ReleaseMonitorError",
                )
                self.assertTrue(result["prior_pair_unchanged"])
                self.assertTrue(result["prior_pair_queryable"])
                self.assertFalse(result["target_release_active"])
                self.assertFalse(result["installation_reported_success"])
        self.assertTrue(evidence["passed"])


class SupportedEnvironmentCriticalJourneyTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _valid_live_rollback_evidence() -> dict:
        return {
            "monitor_id": "knowledge-release-rollback-fault-matrix",
            "mode": "live",
            "passed": True,
            "results": [
                {"phase": phase, "status": "passed"}
                for phase in (
                    "verification",
                    "extraction",
                    "embedding",
                    "indexing",
                    "activation",
                    "late_activation",
                )
            ],
        }

    @staticmethod
    def _observed_live_execution() -> dict:
        return {
            "journey_status": {
                "setup": "passed",
                "supported-answer": "passed",
                "refusal": "passed",
                "evidence-inspection": "passed",
                "history-persistence": "passed",
                "deletion-export": "passed",
                "update-installation": "passed",
            },
            "diagnostics": [],
            "runtime_configuration": {
                "provider_id": "ollama",
                "provider_version": "0.30.6",
                "model": "gemma4:12b",
                "model_identity": {
                    "family": "gemma4",
                    "architecture": "gemma4",
                    "quantization_level": "Q4_K_M",
                },
            },
            "corpus_identity": {
                "knowledge_release_id": "kr-2099-01-01.1",
                "corpus_id": "corpus-2099-01-01.1",
                "source_registry_version": "sr-2099-01-01.1",
                "embedding_model": "embeddinggemma",
                "embedding_vector_dimensions": "768",
                "index_schema_version": "1",
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
            "execution_evidence": {
                "transport": "loopback-bound-process",
                "browser_driver": "playwright",
                "browser_phase_count": 2,
                "app_process_start_count": 2,
                "app_process_stop_count": 2,
                "history_restart_observed": True,
                "browser_evidence_available": True,
            },
        }

    async def test_fixture_mode_is_explicit_and_cannot_be_mistaken_for_live_qualification(self):
        rollback = run_rollback_fault_matrix(mode="fixture", clock=fixed_clock)

        evidence = await run_supported_environment_critical_journeys(
            mode="fixture",
            rollback_evidence=rollback, clock=fixed_clock,
        )

        self.assertEqual(evidence["monitor_id"], "supported-environment-critical-journeys")
        self.assertEqual(evidence["mode"], "fixture-non-live")
        self.assertEqual(evidence["qualification_scope"], "non-live-fixture-only")
        self.assertFalse(evidence["can_qualify_supported_environment"])
        self.assertFalse(evidence["live_provider_calls"])
        self.assertEqual(
            [journey["id"] for journey in evidence["journeys"]],
            [
                "setup",
                "supported-answer",
                "refusal",
                "evidence-inspection",
                "history-persistence",
                "deletion-export",
                "update-installation",
                "rollback",
            ],
        )
        self.assertTrue(all(journey["status"] == "passed" for journey in evidence["journeys"]))
        self.assertEqual(evidence["provider_identity"]["provider_id"], "fixture-local-provider")
        self.assertEqual(evidence["model_identity"]["generation_model"], "fixture-generation")
        self.assertEqual(evidence["corpus_identity"]["knowledge_release_id"], "kr-2099-01-01.1")
        self.assertEqual(evidence["diagnostics"], [])
        serialized = json.dumps(evidence, sort_keys=True).casefold()
        self.assertNotIn("what danish test", serialized)
        self.assertNotIn("do i qualify", serialized)
        self.assertNotIn("a danish language test can be required", serialized)
        self.assertTrue(evidence["passed"])

    async def test_invalid_rollback_evidence_has_a_content_free_diagnostic(self):
        evidence = await run_supported_environment_critical_journeys(
            mode="fixture",
            rollback_evidence={}, clock=fixed_clock,
        )

        self.assertEqual(evidence["failures"], ["rollback"])
        self.assertEqual(
            evidence["diagnostics"],
            [
                {
                    "journey_id": "rollback",
                    "stage": "rollback-evidence-validation",
                    "reason_code": "invalid-rollback-evidence",
                    "exception_type": None,
                }
            ],
        )
        self.assertEqual(
            set(evidence["diagnostics"][0]),
            {"journey_id", "stage", "reason_code", "exception_type"},
        )

    async def test_live_mode_qualifies_only_process_browser_restart_and_observed_identity(self):
        raw_execution = self._observed_live_execution()
        rollback = self._valid_live_rollback_evidence()
        with patch(
            "danish_rag.release_monitors._execute_live_supported_environment_journeys",
            return_value=raw_execution,
            create=True,
        ) as execute_live, patch(
            "danish_rag.release_monitors.install_knowledge_release",
            side_effect=AssertionError("legacy in-process live path executed"),
        ), patch(
            "danish_rag.release_monitors.httpx.AsyncClient.post",
            side_effect=AssertionError("legacy in-process live HTTP executed"),
        ):
            evidence = await run_supported_environment_critical_journeys(
                mode="live",
                rollback_evidence=rollback, clock=fixed_clock,
            )

        execute_live.assert_called_once()
        self.assertTrue(evidence["passed"])
        self.assertTrue(evidence["can_qualify_supported_environment"])
        self.assertEqual(evidence["failures"], [])
        self.assertEqual(
            evidence["supported_environment_identity"],
            {
                "host": "Windows 11 with WSL2 Ubuntu",
                "architecture": "x86-64",
                "python": "3.11+",
                "ollama": "0.30.6+",
                "browser": "evergreen local browser",
            },
        )
        self.assertEqual(
            evidence["observed_environment_identity"]["python_version"],
            "3.12.3",
        )
        self.assertTrue(evidence["environment_identity_validation"]["passed"])
        self.assertEqual(
            evidence["execution_evidence"],
            raw_execution["execution_evidence"],
        )

    async def test_live_mode_fails_closed_without_restart_or_browser_evidence(self):
        raw_execution = self._observed_live_execution()
        rollback = self._valid_live_rollback_evidence()
        raw_execution["execution_evidence"].update(
            {
                "history_restart_observed": False,
                "browser_evidence_available": False,
            }
        )
        with patch(
            "danish_rag.release_monitors._execute_live_supported_environment_journeys",
            return_value=raw_execution,
            create=True,
        ), patch(
            "danish_rag.release_monitors.install_knowledge_release",
            side_effect=AssertionError("legacy in-process live path executed"),
        ), patch(
            "danish_rag.release_monitors.httpx.AsyncClient.post",
            side_effect=AssertionError("legacy in-process live HTTP executed"),
        ):
            evidence = await run_supported_environment_critical_journeys(
                mode="live",
                rollback_evidence=rollback, clock=fixed_clock,
            )

        self.assertFalse(evidence["passed"])
        self.assertFalse(evidence["can_qualify_supported_environment"])
        self.assertIn("history-persistence", evidence["failures"])
        self.assertIn("setup", evidence["failures"])
        serialized = json.dumps(evidence, sort_keys=True).casefold()
        self.assertNotIn("legacy in-process live path executed", serialized)

    async def test_live_report_whitelists_diagnostics_and_model_identity(self):
        raw_execution = self._observed_live_execution()
        raw_execution["journey_status"]["supported-answer"] = "failed"
        raw_execution["diagnostics"] = [
            {
                "journey_id": "supported-answer",
                "stage": "PRIVATE-CONTENT-SENTINEL",
                "reason_code": "PRIVATE-CONTENT-SENTINEL",
                "exception_type": "PRIVATE-CONTENT-SENTINEL /tmp/private",
            }
        ]
        raw_execution["runtime_configuration"]["model_identity"].update(
            {
                "PRIVATE-CONTENT-SENTINEL": "private answer",
            }
        )
        with patch(
            "danish_rag.release_monitors._execute_live_supported_environment_journeys",
            return_value=raw_execution,
        ):
            evidence = await run_supported_environment_critical_journeys(
                mode="live",
                rollback_evidence=self._valid_live_rollback_evidence(), clock=fixed_clock,
            )

        diagnostic = next(
            item
            for item in evidence["diagnostics"]
            if item["journey_id"] == "supported-answer"
        )
        self.assertEqual(diagnostic["stage"], "journey-validation")
        self.assertEqual(diagnostic["reason_code"], "journey-check-failed")
        self.assertIsNone(diagnostic["exception_type"])
        serialized = json.dumps(evidence, sort_keys=True).casefold()
        self.assertNotIn("private-content-sentinel", serialized)
        self.assertNotIn("private answer", serialized)

    async def test_live_process_or_browser_failure_cannot_qualify(self):
        class BrowserLaunchFailure(RuntimeError):
            pass

        with patch(
            "danish_rag.release_monitors._execute_live_supported_environment_journeys",
            side_effect=BrowserLaunchFailure(
                "PRIVATE-CONTENT-SENTINEL /tmp/browser-profile"
            ),
        ):
            evidence = await run_supported_environment_critical_journeys(
                mode="live",
                rollback_evidence=self._valid_live_rollback_evidence(), clock=fixed_clock,
            )

        self.assertFalse(evidence["passed"])
        self.assertFalse(evidence["can_qualify_supported_environment"])
        self.assertEqual(
            evidence["failures"],
            [
                "setup",
                "supported-answer",
                "refusal",
                "evidence-inspection",
                "history-persistence",
                "deletion-export",
                "update-installation",
            ],
        )
        self.assertEqual(
            evidence["diagnostics"][0],
            {
                "journey_id": "setup",
                "stage": "live-process-browser-execution",
                "reason_code": "journey-exception",
                "exception_type": "BrowserLaunchFailure",
            },
        )
        self.assertNotIn(
            "private-content-sentinel",
            json.dumps(evidence, sort_keys=True).casefold(),
        )

    def test_observed_identity_validation_does_not_copy_policy_values(self):
        policy = release_monitors.load_runtime_policy(
            release_monitors.DEFAULT_RUNTIME_POLICY_PATH
        )
        observed = self._observed_live_execution()["observed_environment_identity"]

        validation = release_monitors.validate_observed_supported_environment_identity(
            observed,
            policy,
        )

        self.assertTrue(validation["passed"])
        self.assertEqual(
            validation["browser_release_baseline"]["minimum_chromium_major"],
            150,
        )
        self.assertEqual(validation["observed"]["python_version"], "3.12.3")
        self.assertEqual(validation["normalized"]["python"], "3.11+")
        changed = json.loads(json.dumps(observed))
        changed["architecture"] = "aarch64"
        failed = release_monitors.validate_observed_supported_environment_identity(
            changed,
            policy,
        )
        self.assertFalse(failed["passed"])
        self.assertFalse(failed["checks"]["architecture"])
        stale_browser = json.loads(json.dumps(observed))
        stale_browser["browser_version"] = "149.0.0"
        failed = release_monitors.validate_observed_supported_environment_identity(
            stale_browser,
            policy,
        )
        self.assertFalse(failed["passed"])
        self.assertFalse(failed["checks"]["browser"])

    async def test_setup_exception_reports_only_its_type_and_failure_stages(self):
        class InjectedSetupFailure(RuntimeError):
            pass

        rollback = run_rollback_fault_matrix(mode="fixture", clock=fixed_clock)
        with patch(
            "danish_rag.release_monitors.httpx.AsyncClient.post",
            side_effect=InjectedSetupFailure("PRIVATE-CONTENT-SENTINEL /tmp/private"),
        ):
            evidence = await run_supported_environment_critical_journeys(
                mode="fixture",
                rollback_evidence=rollback, clock=fixed_clock,
            )

        self.assertEqual(
            [diagnostic["journey_id"] for diagnostic in evidence["diagnostics"]],
            evidence["failures"],
        )
        self.assertEqual(
            evidence["diagnostics"][0],
            {
                "journey_id": "setup",
                "stage": "setup-request",
                "reason_code": "journey-exception",
                "exception_type": "InjectedSetupFailure",
            },
        )
        for diagnostic in evidence["diagnostics"][1:]:
            self.assertEqual(diagnostic["stage"], "prerequisite-check")
            self.assertEqual(diagnostic["reason_code"], "prerequisite-failed")
            self.assertIsNone(diagnostic["exception_type"])
        serialized = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("PRIVATE-CONTENT-SENTINEL", serialized)
        self.assertNotIn("/tmp/private", serialized)


class ReleaseMonitorCliTests(unittest.TestCase):
    def test_fixture_cli_writes_non_qualifying_content_free_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_path = Path(temporary) / "release-monitors.json"

            exit_status = main(
                [
                    "--mode",
                    "fixture",
                    "--output",
                    str(output_path),
                    "--generated-at-utc",
                    "2026-07-14T00:00:00Z",
                    "--strict",
                ], clock=fixed_clock
            )

            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_status, 1)
            self.assertTrue(report["component_passed"])
            self.assertFalse(report["strict_passed"])
            self.assertEqual(report["mode"], "fixture-non-live")
            serialized = json.dumps(report, sort_keys=True).casefold()
            self.assertNotIn("what danish test", serialized)
            self.assertNotIn("permanent residence?", serialized)


if __name__ == "__main__":
    unittest.main()
