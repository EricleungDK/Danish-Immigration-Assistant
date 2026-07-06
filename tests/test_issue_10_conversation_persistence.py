import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx

from danish_rag.knowledge_release import (
    BUNDLED_MINIMAL_RELEASE,
    install_minimal_knowledge_release,
)
from danish_rag.local_app import create_app
from danish_rag.provider_setup import ProviderConfiguration, save_provider_configuration


class FixtureAnswerGenerator:
    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        citation_id = evidence[0]["citation_id"]
        return {
            "summary": f"Supported answer for {question}",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": (
                        "Permanent opholdstilladelse can require documentation for "
                        "bestået Prøve i Dansk 2 or an equivalent Danish test."
                    ),
                    "citation_ids": [citation_id],
                },
                {
                    "kind": "interpretation",
                    "text": (
                        "Use the cited official page to verify the requirement that applies "
                        "to your own situation."
                    ),
                    "citation_ids": [citation_id],
                },
            ],
        }


class Issue10ConversationPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.config_path = self.root / "config" / "provider-config.json"
        self.data_dir = self.root / "data"
        self.save_provider("fixture-model-v1")

    def save_provider(self, model: str) -> None:
        save_provider_configuration(
            self.config_path,
            ProviderConfiguration(
                provider_id="openai_compatible",
                endpoint="http://127.0.0.1:1234",
                model=model,
                provider_version=f"{model}-provider",
                model_identity={"id": model},
                capabilities=["generation"],
                validated_at_utc="2026-07-06T12:00:00+00:00",
            ),
        )

    def make_client(self, *, answer_generator: Any | None = None):
        app = create_app(
            config_path=self.config_path,
            data_dir=self.data_dir,
            answer_generator=answer_generator or FixtureAnswerGenerator(),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)
        return client

    def install_release_variant(self, release_id: str) -> None:
        release_dir = self.root / release_id
        shutil.copytree(BUNDLED_MINIMAL_RELEASE, release_dir)
        manifest_path = release_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["knowledge_release_id"] = release_id
        manifest["corpus_id"] = release_id
        manifest["source_registry_version"] = f"sr-{release_id}"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        install_minimal_knowledge_release(self.data_dir, release_dir=release_dir)

    async def post_question(
        self,
        client: httpx.AsyncClient,
        question: str,
        *,
        conversation_id: str | None = None,
    ) -> httpx.Response:
        data = {"question": question}
        if conversation_id:
            data["conversation_id"] = conversation_id
        return await client.post(
            "/ask",
            data=data,
            headers={"Origin": "http://testserver"},
        )

    def conversation_id_from(self, html: str) -> str:
        match = re.search(r'href="/conversations/([^"]+)"', html)
        self.assertIsNotNone(match, html)
        return match.group(1)

    async def test_reopened_conversation_preserves_all_turns_and_historical_provenance(self):
        client = self.make_client()

        first = await self.post_question(
            client,
            "What Danish test do I need for permanent residence?",
        )

        self.assertEqual(first.status_code, 200)
        conversation_id = self.conversation_id_from(first.text)
        self.assertIn("New conversation", first.text)
        self.assertIn("Local History", first.text)
        self.assertIn("What Danish test do I need for permanent residence?", first.text)
        self.assertIn("fixture-model-v1", first.text)
        self.assertIn("Corpus: kr-2026-07-06.1", first.text)
        self.assertIn("Evidence Confidence: High", first.text)
        self.assertIn("Fresh Tomato Score: High", first.text)
        self.assertIn("Answered:", first.text)

        self.save_provider("fixture-model-v2")
        self.install_release_variant("kr-issue-10-fixture.2")

        second = await self.post_question(
            client,
            "Can Prøve i Dansk 2 support permanent residence?",
            conversation_id=conversation_id,
        )

        self.assertEqual(second.status_code, 200)
        self.assertIn("What Danish test do I need for permanent residence?", second.text)
        self.assertIn("Can Prøve i Dansk 2 support permanent residence?", second.text)
        self.assertIn("fixture-model-v1", second.text)
        self.assertIn("fixture-model-v2", second.text)
        self.assertIn("Corpus: kr-2026-07-06.1", second.text)
        self.assertIn("Corpus: kr-issue-10-fixture.2", second.text)

        restarted = self.make_client()
        home = await restarted.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("New conversation", home.text)
        self.assertIn(f'href="/conversations/{conversation_id}"', home.text)

        reopened = await restarted.get(f"/conversations/{conversation_id}")

        self.assertEqual(reopened.status_code, 200)
        self.assertIn("Current Conversation", reopened.text)
        self.assertIn("What Danish test do I need for permanent residence?", reopened.text)
        self.assertIn("Can Prøve i Dansk 2 support permanent residence?", reopened.text)
        self.assertIn("fixture-model-v1", reopened.text)
        self.assertIn("fixture-model-v2", reopened.text)
        self.assertIn("Corpus: kr-2026-07-06.1", reopened.text)
        self.assertIn("Corpus: kr-issue-10-fixture.2", reopened.text)
        self.assertEqual(reopened.text.count("Evidence Confidence: High"), 2)
        self.assertEqual(reopened.text.count("Fresh Tomato Score: High"), 2)
        self.assertEqual(reopened.text.count("Answered:"), 2)


if __name__ == "__main__":
    unittest.main()
