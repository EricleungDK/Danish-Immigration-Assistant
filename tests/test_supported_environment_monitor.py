import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from danish_rag import supported_environment_monitor as monitor


class SupportedEnvironmentMonitorTests(unittest.TestCase):
    def test_live_executor_stops_and_restarts_process_between_browser_phases(self):
        events: list[str] = []

        def prepare_workspace(root: Path) -> monitor.LiveEnvironmentWorkspace:
            events.append("prepare")
            return monitor.LiveEnvironmentWorkspace(
                data_dir=root / "data",
                config_path=root / "provider.json",
                release_catalog_dir=root / "catalog",
                trust_root_path=root / "trust-root.json",
                target_release_id="kr-target",
            )

        applications = iter(("first-process", "second-process"))

        def start_application(**_kwargs):
            application = next(applications)
            events.append(f"start:{application}")
            return application

        def wait_for_application(application, **_kwargs):
            events.append(f"ready:{application}")

        def stop_application(application):
            events.append(f"stop:{application}")
            return True

        def run_browser_phase(**kwargs):
            phase = kwargs["phase"]
            events.append(f"browser:{phase}")
            browser_identity = {"name": "chromium", "version": "140.1"}
            if phase == "before-restart":
                return {
                    "journey_status": {
                        "setup": "passed",
                        "supported-answer": "passed",
                        "refusal": "passed",
                        "evidence-inspection": "passed",
                    },
                    "browser_identity": browser_identity,
                    "runtime_configuration": {
                        "provider_id": "ollama",
                        "provider_version": "0.30.6",
                        "model": "gemma4:12b",
                        "model_identity": {"digest": "sha256:observed"},
                    },
                }
            return {
                "journey_status": {
                    "history-persistence": "passed",
                    "deletion-export": "passed",
                    "update-installation": "passed",
                },
                "browser_identity": browser_identity,
                "corpus_identity": {
                    "knowledge_release_id": "kr-target",
                    "corpus_id": "corpus-target",
                    "source_registry_version": "sr-target",
                    "embedding_model": "embeddinggemma",
                    "embedding_vector_dimensions": "768",
                    "index_schema_version": "1",
                },
            }

        policy = {
            "providers": {
                "initial": {"default_endpoint": "http://127.0.0.1:11434"}
            },
            "models": {"generation": {"initial": "gemma4:12b"}},
        }
        observed = {
            "host_os": "Windows",
            "windows_version": "11",
            "windows_build": "26100",
            "wsl_version": "2",
            "distribution_id": "ubuntu",
            "distribution_version": "24.04",
            "architecture": "x86_64",
            "python_version": "3.12.3",
            "ollama_version": "",
            "browser_name": "",
            "browser_version": "",
        }
        with patch.object(
            monitor, "_start_application", side_effect=start_application
        ), patch.object(
            monitor, "_wait_for_application", side_effect=wait_for_application
        ), patch.object(
            monitor, "_stop_application", side_effect=stop_application
        ), patch.object(
            monitor, "_run_browser_phase", side_effect=run_browser_phase
        ), patch.object(
            monitor,
            "observe_supported_environment_identity",
            return_value=observed,
        ), patch.object(
            monitor, "_available_loopback_port", return_value=18957
        ), patch.object(
            monitor.shutil, "which", return_value="/usr/bin/node"
        ):
            evidence = monitor.execute_live_supported_environment_journeys(
                policy=policy,
                prepare_workspace=prepare_workspace,
            )

        self.assertEqual(
            events,
            [
                "prepare",
                "start:first-process",
                "ready:first-process",
                "browser:before-restart",
                "stop:first-process",
                "start:second-process",
                "ready:second-process",
                "browser:after-restart",
                "stop:second-process",
            ],
        )
        self.assertEqual(evidence["execution_evidence"]["app_process_start_count"], 2)
        self.assertEqual(evidence["execution_evidence"]["app_process_stop_count"], 2)
        self.assertTrue(evidence["execution_evidence"]["history_restart_observed"])
        self.assertEqual(
            evidence["observed_environment_identity"]["browser_version"],
            "140.1",
        )
        self.assertEqual(
            evidence["observed_environment_identity"]["ollama_version"],
            "0.30.6",
        )

    def test_observed_identity_comes_from_host_runtime_facts(self):
        with patch.object(
            monitor,
            "_read_os_release",
            return_value={"ID": "ubuntu", "VERSION_ID": "24.04"},
        ), patch.object(
            monitor.Path,
            "read_text",
            return_value="6.6.0-microsoft-standard-WSL2",
        ), patch.object(
            monitor, "_observed_windows_build", return_value="26100"
        ), patch.object(
            monitor.platform, "machine", return_value="x86_64"
        ), patch.object(
            monitor.platform, "python_version", return_value="3.12.3"
        ):
            observed = monitor.observe_supported_environment_identity()

        self.assertEqual(
            observed,
            {
                "host_os": "Windows",
                "windows_version": "11",
                "windows_build": "26100",
                "wsl_version": "2",
                "distribution_id": "ubuntu",
                "distribution_version": "24.04",
                "architecture": "x86_64",
                "python_version": "3.12.3",
                "ollama_version": "",
                "browser_name": "",
                "browser_version": "",
            },
        )

    def test_browser_phase_output_is_whitelisted_and_content_free(self):
        raw = {
            "journey_status": {
                "setup": "passed",
                "supported-answer": "passed",
                "refusal": "passed",
                "evidence-inspection": "passed",
            },
            "browser_identity": {"name": "chromium", "version": "140.1"},
            "runtime_configuration": {
                "provider_id": "ollama",
                "provider_version": "0.30.6",
                "model": "gemma4:12b",
                "model_identity": {
                    "digest": "sha256:observed",
                    "PRIVATE-CONTENT-SENTINEL": "private answer",
                },
                "question": "PRIVATE-CONTENT-SENTINEL",
            },
            "conversation_id": "PRIVATE-CONTENT-SENTINEL",
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "phase.json"
            output.write_text(json.dumps(raw), encoding="utf-8")
            sanitized = monitor._read_browser_phase_output(
                output,
                phase="before-restart",
            )

        self.assertNotIn(
            "private-content-sentinel",
            json.dumps(sanitized, sort_keys=True).casefold(),
        )
        self.assertEqual(
            sanitized["runtime_configuration"]["model_identity"],
            {"digest": "sha256:observed"},
        )


if __name__ == "__main__":
    unittest.main()
