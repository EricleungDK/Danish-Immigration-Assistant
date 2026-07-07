import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from danish_rag.answer_pipeline import AnswerPipelineError
from danish_rag.conversation_store import ConversationStore
from danish_rag.knowledge_release import (
    ACTIVE_RELEASE_FILE,
    KnowledgeReleaseError,
    install_minimal_knowledge_release,
    load_active_release,
)
from danish_rag.local_app import create_app
from danish_rag.provider_setup import ProviderConfiguration, save_provider_configuration


class SuccessfulAnswerGenerator:
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
            "summary": "The official source supports a Danish language requirement answer.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": (
                        "Permanent opholdstilladelse can require documentation for "
                        "passed Prøve i Dansk 2 or an equivalent Danish test."
                    ),
                    "citation_ids": [citation_id],
                }
            ],
        }


class UnavailableProviderGenerator:
    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        raise AnswerPipelineError(
            "Local generation provider is unavailable. Start the local provider and retry."
        )


class InvalidStructuredOutputGenerator:
    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "summary": "Unsupported structured output.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": "Prøve i Dansk 2 can support permanent residence.",
                    "citation_ids": [],
                }
            ],
        }


class Issue22SafeRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.config_path = self.root / "config" / "provider-config.json"
        self.data_dir = self.root / "data"
        save_provider_configuration(
            self.config_path,
            ProviderConfiguration(
                provider_id="openai_compatible",
                endpoint="http://127.0.0.1:1234",
                model="recovery-fixture-model",
                provider_version="recovery-fixture-provider",
                model_identity={"id": "recovery-fixture-model"},
                capabilities=["generation"],
                validated_at_utc="2026-07-07T12:00:00+00:00",
            ),
        )

    def make_client(self, answer_generator: Any | None = None) -> httpx.AsyncClient:
        app = create_app(
            config_path=self.config_path,
            data_dir=self.data_dir,
            answer_generator=answer_generator or SuccessfulAnswerGenerator(),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)
        return client

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

    def conversation_id_from(self, html: str, title: str) -> str:
        match = re.search(rf'href="/conversations/([^"]+)">{re.escape(title)}', html)
        self.assertIsNotNone(match, html)
        return match.group(1)

    async def create_conversation(self) -> str:
        client = self.make_client()
        response = await self.post_question(
            client,
            "What Danish test do I need for permanent residence?",
        )
        self.assertEqual(response.status_code, 200)
        return self.conversation_id_from(
            response.text,
            "What Danish test do I need for permanent residence?",
        )

    async def test_provider_failure_preserves_question_prior_conversation_and_record(self):
        conversation_id = await self.create_conversation()
        client = self.make_client(UnavailableProviderGenerator())

        response = await self.post_question(
            client,
            "What about Prøve i Dansk 3?",
            conversation_id=conversation_id,
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Local generation provider", response.text)
        self.assertIn("What about Prøve i Dansk 3?", response.text)
        self.assertIn("What Danish test do I need for permanent residence?", response.text)
        payload = (await client.get(f"/conversations/{conversation_id}/export.json")).json()
        self.assertEqual(len(payload["conversation"]["turns"]), 1)

    async def test_validation_failure_preserves_question_prior_conversation_and_record(self):
        conversation_id = await self.create_conversation()
        client = self.make_client(InvalidStructuredOutputGenerator())

        response = await self.post_question(
            client,
            "Can PD2 support permanent residence?",
            conversation_id=conversation_id,
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Structured answer validation failed", response.text)
        self.assertIn("Can PD2 support permanent residence?", response.text)
        self.assertIn("What Danish test do I need for permanent residence?", response.text)
        payload = (await client.get(f"/conversations/{conversation_id}/export.json")).json()
        self.assertEqual(len(payload["conversation"]["turns"]), 1)

    async def test_missing_retrieval_index_preserves_question_prior_conversation_and_record(self):
        conversation_id = await self.create_conversation()
        index_path = self.data_dir / "index" / "kr-2026-07-06.1" / "dense-index.json"
        index_path.unlink()
        client = self.make_client()

        response = await self.post_question(
            client,
            "Can PD2 support permanent residence?",
            conversation_id=conversation_id,
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Local retrieval index is unavailable", response.text)
        self.assertIn("Can PD2 support permanent residence?", response.text)
        self.assertIn("What Danish test do I need for permanent residence?", response.text)
        payload = (await client.get(f"/conversations/{conversation_id}/export.json")).json()
        self.assertEqual(len(payload["conversation"]["turns"]), 1)

    async def test_storage_failures_do_not_report_save_delete_or_export_success(self):
        client = self.make_client()

        with patch(
            "danish_rag.local_app.ConversationStore.save_answer",
            side_effect=OSError("simulated local storage write failure"),
        ):
            failed_save = await self.post_question(
                client,
                "What Danish test do I need for permanent residence?",
            )

        self.assertEqual(failed_save.status_code, 503)
        self.assertIn("Local conversation storage failed while saving", failed_save.text)
        self.assertIn("What Danish test do I need for permanent residence?", failed_save.text)

        conversation_id = await self.create_conversation()
        with patch(
            "danish_rag.local_app.ConversationStore.export_conversation",
            side_effect=OSError("simulated export failure"),
        ):
            failed_export = await client.get(f"/conversations/{conversation_id}/export.json")

        self.assertEqual(failed_export.status_code, 503)
        self.assertNotIn("content-disposition", failed_export.headers)
        self.assertIn("Local conversation storage failed while exporting", failed_export.text)

        with patch(
            "danish_rag.local_app.ConversationStore.delete_conversation",
            side_effect=OSError("simulated delete failure"),
        ):
            failed_delete = await client.post(
                f"/conversations/{conversation_id}/delete",
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            )

        self.assertEqual(failed_delete.status_code, 503)
        self.assertNotEqual(failed_delete.status_code, 303)
        self.assertIn("Local conversation storage failed while deleting", failed_delete.text)

        with patch(
            "danish_rag.local_app.ConversationStore.list_conversations",
            side_effect=OSError("simulated history read failure"),
        ):
            failed_history = await client.get("/")

        self.assertEqual(failed_history.status_code, 200)
        self.assertIn("Local conversation storage failed while opening", failed_history.text)
        self.assertNotIn("No conversation records yet.", failed_history.text)

    def test_interrupted_conversation_write_does_not_expose_partial_record(self):
        def fault_injector(phase: str) -> None:
            if phase == "after_conversation_header":
                raise RuntimeError("simulated interrupted conversation write")

        store = ConversationStore(
            self.data_dir / "conversations.sqlite3",
            fault_injector=fault_injector,
        )

        with self.assertRaisesRegex(RuntimeError, "interrupted conversation write"):
            store.save_answer(
                question="What Danish test do I need?",
                normalized_question="What Danish test do I need?",
                answer={
                    "summary": "fixture",
                    "sections": [],
                    "citations": [],
                    "trust": {},
                },
                model_identity={"provider_id": "fixture", "model": "fixture"},
                corpus_identity="kr-2026-07-06.1",
            )

        self.assertEqual(
            ConversationStore(self.data_dir / "conversations.sqlite3").list_conversations(),
            [],
        )

    def test_mismatched_active_corpus_index_pair_is_rejected(self):
        install_minimal_knowledge_release(self.data_dir)
        active_path = self.data_dir / ACTIVE_RELEASE_FILE
        active = json.loads(active_path.read_text(encoding="utf-8"))
        active["index_path"] = str(self.data_dir / "index" / "wrong-release")
        active_path.write_text(
            json.dumps(active, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(KnowledgeReleaseError, "active corpus/index pair"):
            load_active_release(self.data_dir)


if __name__ == "__main__":
    unittest.main()
