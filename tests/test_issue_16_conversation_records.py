import re
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

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
                    "text": "Use the cited official page to verify the requirement.",
                    "citation_ids": [citation_id],
                },
            ],
        }


class Issue16ConversationRecordControlTests(unittest.IsolatedAsyncioTestCase):
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
                model="records-fixture-model",
                provider_version="records-fixture-provider",
                model_identity={"id": "records-fixture-model"},
                capabilities=["generation"],
                validated_at_utc="2026-07-07T10:00:00+00:00",
            ),
        )

    def make_client(self) -> httpx.AsyncClient:
        app = create_app(
            config_path=self.config_path,
            data_dir=self.data_dir,
            answer_generator=FixtureAnswerGenerator(),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)
        return client

    async def post_question(self, client: httpx.AsyncClient, question: str) -> httpx.Response:
        return await client.post(
            "/ask",
            data={"question": question},
            headers={"Origin": "http://testserver"},
        )

    def conversation_id_from(self, html: str, title: str) -> str:
        match = re.search(rf'href="/conversations/([^"]+)">{re.escape(title)}', html)
        self.assertIsNotNone(match, html)
        return match.group(1)

    async def test_export_includes_turns_provenance_citations_and_trust_indicators(self):
        client = self.make_client()
        response = await self.post_question(
            client,
            "What Danish test do I need for permanent residence?",
        )
        self.assertEqual(response.status_code, 200)
        conversation_id = self.conversation_id_from(
            response.text,
            "What Danish test do I need for permanent residence?",
        )

        with patch("urllib.request.urlopen") as urlopen:
            export_response = await client.get(f"/conversations/{conversation_id}/export.json")

        urlopen.assert_not_called()
        self.assertEqual(export_response.status_code, 200)
        self.assertIn("attachment", export_response.headers["content-disposition"])
        payload = export_response.json()
        conversation = payload["conversation"]
        turn = conversation["turns"][0]

        self.assertEqual(payload["export_schema"], "danish-rag.conversation-record.v1")
        self.assertEqual(conversation["id"], conversation_id)
        self.assertEqual(
            turn["question"],
            "What Danish test do I need for permanent residence?",
        )
        self.assertEqual(turn["model_identity"]["model"], "records-fixture-model")
        self.assertEqual(turn["corpus_version"], "kr-2026-07-06.1")
        self.assertEqual(turn["citations"][0]["publisher"], "SIRI")
        self.assertEqual(turn["answer"]["trust"]["evidence_confidence"], "High")
        self.assertEqual(turn["trust_indicators"]["evidence_confidence"], "High")
        self.assertEqual(turn["trust_indicators"]["fresh_tomato_score"], "High")

    async def test_deleting_one_conversation_removes_it_from_navigation_and_exports_after_restart(self):
        client = self.make_client()
        first = await self.post_question(client, "What Danish test do I need for permanent residence?")
        second = await self.post_question(client, "Can Prøve i Dansk 2 support permanent residence?")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_id = self.conversation_id_from(
            first.text,
            "What Danish test do I need for permanent residence?",
        )
        second_id = self.conversation_id_from(
            second.text,
            "Can Prøve i Dansk 2 support permanent residence?",
        )

        with patch("urllib.request.urlopen") as urlopen:
            delete_response = await client.post(
                f"/conversations/{first_id}/delete",
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            )

        urlopen.assert_not_called()
        self.assertEqual(delete_response.status_code, 303)

        restarted = self.make_client()
        home = await restarted.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertNotIn(f'href="/conversations/{first_id}"', home.text)
        self.assertIn(f'href="/conversations/{second_id}"', home.text)

        deleted = await restarted.get(f"/conversations/{first_id}")
        self.assertEqual(deleted.status_code, 404)

        all_export = await restarted.get("/conversations/export.json")
        self.assertEqual(all_export.status_code, 200)
        payload = all_export.json()
        exported_ids = [item["id"] for item in payload["conversations"]]
        self.assertNotIn(first_id, exported_ids)
        self.assertEqual(exported_ids, [second_id])

    async def test_delete_all_requires_scope_confirmation_and_leaves_no_accessible_records(self):
        client = self.make_client()
        first = await self.post_question(client, "What Danish test do I need for permanent residence?")
        second = await self.post_question(client, "Can Prøve i Dansk 2 support permanent residence?")
        first_id = self.conversation_id_from(
            first.text,
            "What Danish test do I need for permanent residence?",
        )
        second_id = self.conversation_id_from(
            second.text,
            "Can Prøve i Dansk 2 support permanent residence?",
        )

        home = await client.get("/")
        self.assertIn("Type DELETE ALL LOCAL CONVERSATIONS", home.text)

        rejected = await client.post(
            "/conversations/delete-all",
            data={"confirmation": "delete everything"},
            headers={"Origin": "http://testserver"},
        )
        self.assertEqual(rejected.status_code, 422)

        with patch("urllib.request.urlopen") as urlopen:
            accepted = await client.post(
                "/conversations/delete-all",
                data={"confirmation": "DELETE ALL LOCAL CONVERSATIONS"},
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            )

        urlopen.assert_not_called()
        self.assertEqual(accepted.status_code, 303)

        restarted = self.make_client()
        restarted_home = await restarted.get("/")
        self.assertEqual(restarted_home.status_code, 200)
        self.assertIn("No conversation records yet.", restarted_home.text)
        self.assertNotIn(f'href="/conversations/{first_id}"', restarted_home.text)
        self.assertNotIn(f'href="/conversations/{second_id}"', restarted_home.text)

        all_export = await restarted.get("/conversations/export.json")
        self.assertEqual(all_export.status_code, 200)
        self.assertEqual(all_export.json()["conversations"], [])
        self.assertEqual((await restarted.get(f"/conversations/{first_id}")).status_code, 404)
        self.assertEqual((await restarted.get(f"/conversations/{second_id}")).status_code, 404)


if __name__ == "__main__":
    unittest.main()
