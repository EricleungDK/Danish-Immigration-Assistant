import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx

from danish_rag.answer_pipeline import answer_schema
from danish_rag.conversation_store import ConversationStore
from danish_rag.knowledge_release import install_minimal_knowledge_release
from danish_rag.local_app import create_app
from danish_rag.provider_setup import ProviderConfiguration, save_provider_configuration
from danish_rag.retrieval import HybridRetriever


class FixtureAnswerGenerator:
    def __init__(self, *, invalid: bool = False) -> None:
        self.invalid = invalid
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "question": question,
                "normalized_question": normalized_question,
                "evidence_ids": [item["document_id"] for item in evidence],
                "provider_id": configuration.provider_id,
                "model": configuration.model,
                "schema": schema,
            }
        )
        if self.invalid:
            return {
                "summary": "Permanent residence language rules need official evidence.",
                "sections": [
                    {
                        "kind": "official_fact",
                        "text": "Prøve i Dansk 2 can support permanent residence.",
                        "citation_ids": [],
                    }
                ],
            }

        citation_id = evidence[0]["citation_id"]
        return {
            "summary": "The official source says Prøve i Dansk 2 can be relevant for permanent residence.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": (
                        "Permanent opholdstilladelse can require documentation for having "
                        "passed Prøve i Dansk 2 or an equivalent Danish test."
                    ),
                    "citation_ids": [citation_id],
                },
                {
                    "kind": "interpretation",
                    "text": (
                        "In practical terms, treat Prøve i Dansk 2 as the Danish term to look "
                        "for on the official page, but verify your own situation with the authority."
                    ),
                    "citation_ids": [citation_id],
                },
            ],
        }


class Issue9AnswerPathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.config_path = root / "config" / "provider-config.json"
        self.data_dir = root / "data"
        save_provider_configuration(
            self.config_path,
            ProviderConfiguration(
                provider_id="openai_compatible",
                endpoint="http://127.0.0.1:1234",
                model="fixture-model",
                provider_version="fixture-provider",
                model_identity={"id": "fixture-model"},
                capabilities=["generation"],
                validated_at_utc="2026-07-06T12:00:00+00:00",
            ),
        )

    def make_client(self, answer_generator: FixtureAnswerGenerator):
        app = create_app(
            config_path=self.config_path,
            data_dir=self.data_dir,
            answer_generator=answer_generator,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)
        return client

    async def test_minimal_release_installs_and_hybrid_retrieval_finds_supported_source(self):
        installation = install_minimal_knowledge_release(self.data_dir)

        self.assertEqual(installation["manifest"]["knowledge_release_id"], "kr-2026-07-06.1")
        self.assertEqual(installation["index"]["retrieval"], "hybrid")
        self.assertEqual(installation["index"]["lexical_engine"], "sqlite-fts5")
        self.assertEqual(installation["index"]["dense_engine"], "local-dense-json")
        self.assertEqual(installation["index"]["embedding_model"], "embeddinggemma")
        self.assertEqual(installation["index"]["corpus_identity"], installation["manifest"]["corpus_id"])

        retriever = HybridRetriever.from_data_dir(self.data_dir)
        results = retriever.retrieve("What Danish test do I need for permanent residence?")

        self.assertTrue(results)
        self.assertEqual(
            results[0]["document_id"],
            "di-rag-doc-permanent-residence-language",
        )
        self.assertEqual(results[0]["publisher"], "SIRI")
        self.assertIn("Prøve i Dansk 2", results[0]["content"])

    async def test_supported_question_renders_cited_answer_and_persists_record(self):
        generator = FixtureAnswerGenerator()
        client = self.make_client(generator)

        response = await client.post(
            "/ask",
            data={"question": "What Danish test do I need for permanent residence?"},
            headers={"Origin": "http://testserver"},
        )

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertEqual(generator.calls[0]["provider_id"], "openai_compatible")
        self.assertEqual(generator.calls[0]["model"], "fixture-model")
        self.assertIn("di-rag-doc-permanent-residence-language", generator.calls[0]["evidence_ids"])
        self.assertEqual(generator.calls[0]["schema"], answer_schema())
        self.assertIn("Current Conversation", html)
        self.assertIn("Prøve i Dansk 2", html)
        self.assertIn("Official fact", html)
        self.assertIn("Interpretation", html)
        self.assertIn("Permanent residence language requirements", html)
        self.assertIn("SIRI", html)
        self.assertIn("https://www.nyidanmark.dk/da/Du-vil-ansoege/Permanent-ophold", html)
        self.assertIn("Checked: 2026-06-15", html)
        self.assertIn("Corpus: kr-2026-07-06.1", html)
        self.assertIn("Evidence Confidence: High", html)
        self.assertIn("Fresh Tomato Score: High", html)
        self.assertIn("fixture-model", html)

        store = ConversationStore(self.data_dir / "conversations.sqlite3")
        conversations = store.list_conversations()
        self.assertEqual(len(conversations), 1)
        record = store.get_conversation(conversations[0]["id"])
        self.assertEqual(record["question"], "What Danish test do I need for permanent residence?")
        self.assertEqual(record["model_identity"]["model"], "fixture-model")
        self.assertEqual(record["corpus_identity"], "kr-2026-07-06.1")
        self.assertEqual(record["answer"]["citations"][0]["publisher"], "SIRI")

        restarted = httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_app(config_path=self.config_path, data_dir=self.data_dir)
            ),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(restarted.aclose)
        reopened = await restarted.get(f"/conversations/{conversations[0]['id']}")

        self.assertEqual(reopened.status_code, 200)
        self.assertIn("What Danish test do I need for permanent residence?", reopened.text)
        self.assertIn("Prøve i Dansk 2", reopened.text)
        self.assertIn("Corpus: kr-2026-07-06.1", reopened.text)

    async def test_validation_failure_preserves_question_and_does_not_persist_answer(self):
        generator = FixtureAnswerGenerator(invalid=True)
        client = self.make_client(generator)

        response = await client.post(
            "/ask",
            data={"question": "What Danish test do I need for permanent residence?"},
            headers={"Origin": "http://testserver"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Answer validation failed", response.text)
        self.assertIn("Retry", response.text)
        self.assertIn(
            "What Danish test do I need for permanent residence?",
            response.text,
        )
        self.assertEqual(
            ConversationStore(self.data_dir / "conversations.sqlite3").list_conversations(),
            [],
        )


if __name__ == "__main__":
    unittest.main()
