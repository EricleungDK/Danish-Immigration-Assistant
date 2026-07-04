import json
import unittest
from pathlib import Path

from danish_rag.runtime_policy import load_runtime_policy
from danish_rag.runtime_probe import ProbeResult, run_runtime_probe


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "runtime-policy.json"


class FakeOllamaClient:
    def __init__(
        self,
        *,
        version="0.30.6",
        show=None,
        chat_content=None,
        failure=None,
    ):
        self.version = version
        self.show = show if show is not None else {
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
        self.chat_content = chat_content or json.dumps(
            {"runtime_baseline": "mvp-runtime-baseline-issue-26", "status": "ok"}
        )
        self.failure = failure
        self.chat_calls = 0

    def get_version(self):
        if self.failure == "service":
            raise ConnectionError("connection refused")
        return {"version": self.version}

    def show_model(self, model):
        if self.failure == "model":
            raise FileNotFoundError(model)
        return self.show

    def chat_structured(self, *, model, schema, messages):
        self.chat_calls += 1
        if self.failure == "chat":
            raise RuntimeError("chat failed")
        return {"message": {"content": self.chat_content}}


class RuntimeProbeTests(unittest.TestCase):
    def test_probe_succeeds_with_version_model_completion_and_structured_json(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(policy, client=FakeOllamaClient())

        self.assertIsInstance(result, ProbeResult)
        self.assertEqual(result.exit_status, 0)
        self.assertEqual(result.provider["version"], "0.30.6")
        self.assertEqual(result.model["name"], "gemma4:12b")
        self.assertEqual(result.model["identity"]["family"], "gemma4")
        self.assertEqual(result.model["identity"]["architecture"], "gemma4")
        self.assertEqual(result.model["identity"]["quantization_level"], "Q4_K_M")
        self.assertIn("completion", result.model["capabilities"])
        self.assertEqual(result.structured_response["status"], "ok")

    def test_probe_reports_missing_service_with_actionable_diagnostic(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(policy, client=FakeOllamaClient(failure="service"))

        self.assertEqual(result.exit_status, 2)
        self.assertIn("Ollama service is unreachable", result.diagnostic)
        self.assertIn("127.0.0.1:11434", result.diagnostic)
        self.assertIn("Start Ollama", result.diagnostic)

    def test_probe_reports_incompatible_ollama_version_with_required_floor(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(policy, client=FakeOllamaClient(version="0.30.5"))

        self.assertEqual(result.exit_status, 3)
        self.assertIn("Upgrade Ollama to 0.30.6 or newer", result.diagnostic)

    def test_probe_reports_missing_generation_model_with_pull_command(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(policy, client=FakeOllamaClient(failure="model"))

        self.assertEqual(result.exit_status, 4)
        self.assertIn("gemma4:12b is not installed", result.diagnostic)
        self.assertIn("ollama pull gemma4:12b", result.diagnostic)

    def test_probe_reports_invalid_structured_output(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(
            policy,
            client=FakeOllamaClient(chat_content=json.dumps({"status": "ok"})),
        )

        self.assertEqual(result.exit_status, 5)
        self.assertIn("structured JSON response did not match", result.diagnostic)

    def test_probe_rejects_structured_output_with_extra_fields(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(
            policy,
            client=FakeOllamaClient(
                chat_content=json.dumps(
                    {
                        "runtime_baseline": "mvp-runtime-baseline-issue-26",
                        "status": "ok",
                        "extra": "not allowed",
                    }
                )
            ),
        )

        self.assertEqual(result.exit_status, 5)
        self.assertIn("unexpected field(s): extra", result.diagnostic)
        self.assertIn("issue #26 schema", result.diagnostic)

    def test_probe_rejects_model_without_completion_capability_before_chat(self):
        policy = load_runtime_policy(POLICY_PATH)
        client = FakeOllamaClient(
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

        self.assertEqual(result.exit_status, 4)
        self.assertEqual(client.chat_calls, 0)
        self.assertEqual(result.model["capabilities"], ["thinking"])
        self.assertIn("completion", result.diagnostic)
        self.assertIn("issue #26", result.diagnostic)

    def test_probe_rejects_non_object_structured_json(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(
            policy,
            client=FakeOllamaClient(chat_content=json.dumps(["not", "an", "object"])),
        )

        self.assertEqual(result.exit_status, 5)
        self.assertIn("chat response JSON was not an object", result.diagnostic)

    def test_probe_rejects_structured_output_with_wrong_values(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(
            policy,
            client=FakeOllamaClient(
                chat_content=json.dumps(
                    {
                        "runtime_baseline": "different-baseline",
                        "status": "ok",
                    }
                )
            ),
        )

        self.assertEqual(result.exit_status, 5)
        self.assertIn("runtime_baseline expected", result.diagnostic)

    def test_probe_reports_mismatched_generation_model_family(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(
            policy,
            client=FakeOllamaClient(
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

        self.assertEqual(result.exit_status, 4)
        self.assertIn("model identity", result.diagnostic)
        self.assertIn("family", result.diagnostic)
        self.assertIn("gemma4", result.diagnostic)

    def test_probe_reports_missing_generation_model_identity_evidence(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(
            policy,
            client=FakeOllamaClient(
                show={
                    "details": {},
                    "model_info": {},
                    "capabilities": ["completion"],
                }
            ),
        )

        self.assertEqual(result.exit_status, 4)
        self.assertIn("missing identity evidence", result.diagnostic)
        self.assertIn("/api/show", result.diagnostic)

    def test_probe_reports_wrong_generation_model_quantization(self):
        policy = load_runtime_policy(POLICY_PATH)

        result = run_runtime_probe(
            policy,
            client=FakeOllamaClient(
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

        self.assertEqual(result.exit_status, 4)
        self.assertIn("quantization", result.diagnostic)
        self.assertIn("Q4_K_M", result.diagnostic)


if __name__ == "__main__":
    unittest.main()
