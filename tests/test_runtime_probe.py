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
        self.show = show or {
            "details": {"family": "gemma4"},
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

    def get_version(self):
        if self.failure == "service":
            raise ConnectionError("connection refused")
        return {"version": self.version}

    def show_model(self, model):
        if self.failure == "model":
            raise FileNotFoundError(model)
        return self.show

    def chat_structured(self, *, model, schema, messages):
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


if __name__ == "__main__":
    unittest.main()
