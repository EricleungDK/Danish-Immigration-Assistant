import os
import tempfile
import time
import unittest
from pathlib import Path

import httpx

from danish_rag.answer_pipeline import LocalProviderAnswerGenerator
from danish_rag.conversation_store import ConversationStore
from danish_rag.local_app import create_app
from danish_rag.provider_setup import ProviderConfiguration, save_provider_configuration
from danish_rag.runtime_policy import load_runtime_policy


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "runtime-policy.json"
LIVE_GATE_ENV = "DI_RAG_RUN_LIVE_ANSWER_SMOKE"


@unittest.skipUnless(
    os.environ.get(LIVE_GATE_ENV) == "1",
    f"set {LIVE_GATE_ENV}=1 to run the live local answer-generation smoke test",
)
class LiveAnswerGenerationSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_local_llm_generates_valid_evidence_bounded_answer_through_app(self):
        policy = load_runtime_policy(POLICY_PATH)
        provider_policy = policy["providers"]["initial"]
        generation_policy = policy["models"]["generation"]
        timeout_seconds = 90.0

        with tempfile.TemporaryDirectory(prefix="di-rag-live-answer-") as tempdir:
            root = Path(tempdir)
            data_dir = root / "data"
            config_path = root / "provider-config.json"
            save_provider_configuration(
                config_path,
                ProviderConfiguration(
                    provider_id=provider_policy["id"],
                    endpoint=provider_policy["default_endpoint"],
                    model=generation_policy["initial"],
                    provider_version=provider_policy["minimum_version"],
                    model_identity=generation_policy["identity"],
                    capabilities=["generation"],
                    validated_at_utc="live-smoke-test",
                ),
            )
            app = create_app(
                config_path=config_path,
                data_dir=data_dir,
                answer_generator=LocalProviderAnswerGenerator(timeout_seconds=timeout_seconds),
            )
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            )

            started = time.perf_counter()
            response = await client.post(
                "/ask",
                data={"question": "What Danish test do I need for permanent residence?"},
                headers={"Origin": "http://testserver"},
            )
            elapsed_seconds = time.perf_counter() - started
            await client.aclose()

            conversations = ConversationStore(
                data_dir / "conversations.sqlite3"
            ).list_conversations()

        self.assertLess(elapsed_seconds, timeout_seconds)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Current Conversation", response.text)
        self.assertIn("Provider: ollama", response.text)
        self.assertIn("Model: gemma4:12b", response.text)
        self.assertIn("Corpus: kr-2026-07-06.1", response.text)
        self.assertIn("Permanent residence language requirements", response.text)
        self.assertIn("Evidence Confidence: High", response.text)
        self.assertIn("Fresh Tomato Score: High", response.text)
        self.assertEqual(len(conversations), 1)


if __name__ == "__main__":
    unittest.main()
