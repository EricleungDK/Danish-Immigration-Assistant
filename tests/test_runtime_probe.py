import json
import unittest
from pathlib import Path
from typing import get_type_hints

from danish_rag.local_model_transport import (
    FakeTransport,
    ModelMissingError,
    ModelUnreachableError,
    OllamaTransport,
)
from danish_rag.privacy_boundary import PrivacyBoundaryError
from danish_rag.runtime_policy import load_runtime_policy
from danish_rag.runtime_probe import (
    EnvironmentEvidence,
    ProbeExitStatus,
    ProbeResult,
    run_runtime_probe,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "runtime-policy.json"


def scripted_transport(
    *,
    version="0.30.6",
    show=None,
    chat_content=None,
    failure=None,
):
    default_show = {
        "details": {
            "families": ["gemma4"],
            "family": "gemma4",
            "quantization_level": "Q4_K_M",
        },
        "model_info": {
            "general.architecture": "gemma4",
            "general.parameter_count": 11900000000,
        },
        "capabilities": ["completion"],
    }
    transport = FakeTransport()
    if failure == "service":
        transport.queue_error(ModelUnreachableError("connection refused"), operation="version")
        return transport
    transport.queue_version(version)
    if failure == "model":
        transport.queue_error(ModelMissingError("gemma4:12b"), operation="show_model")
        return transport
    transport.queue_show(show if show is not None else default_show)
    if failure == "chat":
        transport.queue_error(RuntimeError("chat failed"), operation="chat")
    else:
        transport.queue_chat(
            chat_content
            or json.dumps(
                {"runtime_baseline": "mvp-runtime-baseline-issue-26", "status": "ok"}
            )
        )
    return transport


def chat_requests(transport):
    return [r for r in transport.requests if r.operation == "chat"]


class RuntimeProbeTests(unittest.TestCase):
    def test_probe_requests_structured_chat_through_the_transport(self):
        transport = scripted_transport()

        run_runtime_probe(load_runtime_policy(POLICY_PATH), client=transport)

        self.assertEqual(
            [request.operation for request in transport.requests],
            ["version", "show_model", "chat"],
        )
        chat = chat_requests(transport)[0].params
        self.assertEqual(chat["model"], "gemma4:12b")
        self.assertEqual(chat["schema"]["required"], ["runtime_baseline", "status"])

    def test_probe_builds_a_loopback_ollama_transport_by_default(self):
        from danish_rag.runtime_probe import OllamaClient

        self.assertTrue(issubclass(OllamaClient, OllamaTransport))
        with self.assertRaises(PrivacyBoundaryError):
            OllamaClient("http://example.com:11434")

    def test_probe_result_uses_named_environment_evidence_type(self):
        self.assertIs(
            get_type_hints(ProbeResult)["environment"],
            EnvironmentEvidence,
        )
        with self.assertRaises(TypeError):
            ProbeResult(
                exit_status=ProbeExitStatus.INCOMPLETE,
                diagnostic="environment evidence is required",
            )

    def test_probe_succeeds_with_version_model_completion_and_structured_json(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(policy, client=scripted_transport())

        self.assertIsInstance(result, ProbeResult)
        self.assertEqual(result.exit_status, ProbeExitStatus.PASSED)
        self.assertEqual(json.loads(json.dumps(result.to_dict()))["exit_status"], 0)
        self.assertEqual(result.provider["version"], "0.30.6")
        self.assertEqual(result.model["name"], "gemma4:12b")
        self.assertEqual(result.model["identity"]["family"], "gemma4")
        self.assertEqual(result.model["identity"]["architecture"], "gemma4")
        self.assertEqual(result.model["identity"]["quantization_level"], "Q4_K_M")
        self.assertIn("completion", result.model["capabilities"])
        self.assertEqual(result.structured_response["status"], "ok")

    def test_probe_reports_missing_service_with_actionable_diagnostic(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(policy, client=scripted_transport(failure="service"))

        self.assertEqual(result.exit_status, ProbeExitStatus.PROVIDER_UNREACHABLE)
        self.assertIn("Ollama service is unreachable", result.diagnostic)
        self.assertIn("127.0.0.1:11434", result.diagnostic)
        self.assertIn("Start Ollama", result.diagnostic)

    def test_probe_reports_incompatible_ollama_version_with_required_floor(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(policy, client=scripted_transport(version="0.30.5"))

        self.assertEqual(result.exit_status, ProbeExitStatus.PROVIDER_VERSION_UNSUPPORTED)
        self.assertIn("Upgrade Ollama to 0.30.6 or newer", result.diagnostic)

    def test_probe_reports_missing_generation_model_with_pull_command(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(policy, client=scripted_transport(failure="model"))

        self.assertEqual(result.exit_status, ProbeExitStatus.MODEL_UNAVAILABLE)
        self.assertIn("gemma4:12b is not installed", result.diagnostic)
        self.assertIn("ollama pull gemma4:12b", result.diagnostic)

    def test_probe_reports_invalid_structured_output(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(
            policy,
            client=scripted_transport(chat_content=json.dumps({"status": "ok"})),
        )

        self.assertEqual(result.exit_status, ProbeExitStatus.STRUCTURED_RESPONSE_INVALID)
        self.assertIn("structured JSON response did not match", result.diagnostic)

    def test_probe_rejects_structured_output_with_extra_fields(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(
            policy,
            client=scripted_transport(
                chat_content=json.dumps(
                    {
                        "runtime_baseline": "mvp-runtime-baseline-issue-26",
                        "status": "ok",
                        "extra": "not allowed",
                    }
                )
            ),
        )

        self.assertEqual(result.exit_status, ProbeExitStatus.STRUCTURED_RESPONSE_INVALID)
        self.assertIn("unexpected field(s): extra", result.diagnostic)
        self.assertIn("issue #26 schema", result.diagnostic)

    def test_probe_rejects_model_without_completion_capability_before_chat(self):
        policy = load_runtime_policy(POLICY_PATH)
        client = scripted_transport(
            show={
                "details": {
                    "family": "gemma4",
                    "quantization_level": "Q4_K_M",
                },
                "model_info": {"general.architecture": "gemma4"},
                "capabilities": ["thinking"],
            }
        )

        result = run_runtime_probe(policy, client=client)

        self.assertEqual(result.exit_status, ProbeExitStatus.MODEL_UNAVAILABLE)
        self.assertEqual(chat_requests(client), [])
        self.assertEqual(result.model["capabilities"], ["thinking"])
        self.assertIn("completion", result.diagnostic)
        self.assertIn("issue #26", result.diagnostic)

    def test_probe_rejects_non_object_structured_json(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(
            policy,
            client=scripted_transport(chat_content=json.dumps(["not", "an", "object"])),
        )

        self.assertEqual(result.exit_status, ProbeExitStatus.STRUCTURED_RESPONSE_INVALID)
        self.assertIn("chat response JSON was not an object", result.diagnostic)

    def test_probe_rejects_structured_output_with_wrong_values(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(
            policy,
            client=scripted_transport(
                chat_content=json.dumps(
                    {
                        "runtime_baseline": "different-baseline",
                        "status": "ok",
                    }
                )
            ),
        )

        self.assertEqual(result.exit_status, ProbeExitStatus.STRUCTURED_RESPONSE_INVALID)
        self.assertIn("runtime_baseline expected", result.diagnostic)

    def test_probe_reports_mismatched_generation_model_family(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(
            policy,
            client=scripted_transport(
                show={
                    "details": {
                        "family": "llama",
                        "quantization_level": "Q4_K_M",
                    },
                    "model_info": {"general.architecture": "gemma4"},
                    "capabilities": ["completion"],
                }
            ),
        )

        self.assertEqual(result.exit_status, ProbeExitStatus.MODEL_UNAVAILABLE)
        self.assertIn("model identity", result.diagnostic)
        self.assertIn("family", result.diagnostic)
        self.assertIn("gemma4", result.diagnostic)

    def test_probe_reports_missing_generation_model_identity_evidence(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(
            policy,
            client=scripted_transport(
                show={
                    "details": {},
                    "model_info": {},
                    "capabilities": ["completion"],
                }
            ),
        )

        self.assertEqual(result.exit_status, ProbeExitStatus.MODEL_UNAVAILABLE)
        self.assertIn("missing identity evidence", result.diagnostic)
        self.assertIn("/api/show", result.diagnostic)

    def test_probe_reports_wrong_generation_model_quantization(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(
            policy,
            client=scripted_transport(
                show={
                    "details": {
                        "family": "gemma4",
                        "quantization_level": "Q8_0",
                    },
                    "model_info": {"general.architecture": "gemma4"},
                    "capabilities": ["completion"],
                }
            ),
        )

        self.assertEqual(result.exit_status, ProbeExitStatus.MODEL_UNAVAILABLE)
        self.assertIn("quantization", result.diagnostic)
        self.assertIn("Q4_K_M", result.diagnostic)


if __name__ == "__main__":
    unittest.main()
