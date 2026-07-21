import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx

from danish_rag.answer_pipeline import AnswerService
from danish_rag.conversation_store import ConversationStore
from danish_rag.knowledge_release import install_minimal_knowledge_release
from danish_rag.local_app import create_app
from danish_rag.provider_setup import ProviderConfiguration, save_provider_configuration
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture


def provider_configuration(model: str = "fixture-model") -> ProviderConfiguration:
    return ProviderConfiguration(
        provider_id="openai_compatible",
        endpoint="http://127.0.0.1:1234",
        model=model,
        provider_version="fixture-provider",
        model_identity={"id": model},
        capabilities=["generation"],
        validated_at_utc="2026-07-06T12:00:00+00:00",
    )


def evidence_fixture(citation_id: str = "language-source") -> dict[str, object]:
    return {
        "citation_id": citation_id,
        "document_id": citation_id,
        "source_id": f"source-{citation_id}",
        "title": "Permanent residence language requirements",
        "publisher": "SIRI",
        "official_url": "https://www.nyidanmark.dk/da/Du-vil-ansoege/Permanent-ophold",
        "checked_at_utc": "2026-06-15T09:00:00Z",
        "knowledge_release_id": "kr-fixture",
        "corpus_identity": "kr-fixture",
        "review_state": "approved-current",
        "source_health": "healthy",
        "agreement_state": "supports",
        "topic_tags": ["permanent-residence", "language-requirement"],
        "content": (
            "Permanent opholdstilladelse can require documentation for bestået "
            "Prøve i Dansk 2 or an equivalent Danish test."
        ),
    }


class RecordingRetriever:
    manifest = {"corpus_id": "kr-fixture"}

    def __init__(self) -> None:
        self.calls: list[str] = []

    def retrieve(self, question: str) -> list[dict[str, object]]:
        self.calls.append(question)
        return [evidence_fixture()]


class RecordingGenerator:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.fail_on_call = fail_on_call
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
                "model": configuration.model,
                "evidence_ids": [item["citation_id"] for item in evidence],
            }
        )
        citation_id = evidence[0]["citation_id"]
        if self.fail_on_call and len(self.calls) == self.fail_on_call:
            return {
                "summary": "This response intentionally fails validation.",
                "sections": [
                    {
                        "kind": "official_fact",
                        "text": "An official fact without an adjacent citation.",
                        "citation_ids": [],
                    }
                ],
            }
        return {
            "summary": "The official source supports a language-requirement answer.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": (
                        "Permanent opholdstilladelse can require documentation for "
                        "bestået Prøve i Dansk 2 or an equivalent Danish test."
                    ),
                    "citation_ids": [citation_id],
                }
            ],
        }


class Issue14FollowUpServiceTests(unittest.TestCase):
    def test_concise_follow_up_uses_local_context_and_reruns_retrieval(self):
        retriever = RecordingRetriever()
        generator = RecordingGenerator()
        prior_turns = [
            {
                "turn_index": 1,
                "question": "What Danish test do I need for permanent residence?",
                "answer": {
                    "response_kind": "answer",
                    "summary": "Prior summary must stay out of follow-up context.",
                    "sections": [
                        {
                            "kind": "official_fact",
                            "text": "Prior answer text must stay local to the record.",
                            "citation_ids": ["old-source"],
                        }
                    ],
                },
                "model_identity": {"model": "old-model"},
                "corpus_identity": "old-corpus",
            }
        ]

        result = AnswerService(retriever=retriever, generator=generator).answer(
            "What about PD3?",
            provider_configuration(),
            conversation_turns=prior_turns,
        )

        self.assertEqual(result.question, "What about PD3?")
        self.assertEqual(len(retriever.calls), 1)
        self.assertEqual(len(generator.calls), 1)
        retrieval_question = retriever.calls[0]
        self.assertIn("previous question", retrieval_question.casefold())
        self.assertIn("What Danish test do I need for permanent residence?", retrieval_question)
        self.assertIn("Follow-up question: What about PD3?", retrieval_question)
        self.assertNotIn("Prior answer text", retrieval_question)
        self.assertNotIn("old-corpus", retrieval_question)
        self.assertEqual(generator.calls[0]["question"], retrieval_question)
        self.assertEqual(result.answer["citations"][0]["citation_id"], "language-source")
        self.assertEqual(result.answer["trust"]["evidence_confidence"], "High")

    def test_suggested_follow_ups_are_stored_and_stay_inside_evidence_boundary(self):
        result = AnswerService(
            retriever=RecordingRetriever(),
            generator=RecordingGenerator(),
        ).answer(
            "What Danish test do I need for permanent residence?",
            provider_configuration(),
        )

        suggestions = result.answer["suggested_follow_ups"]

        self.assertTrue(suggestions)
        self.assertIn("Prøve i Dansk 2", " ".join(suggestions))
        self.assertIn("cited source", " ".join(suggestions).casefold())
        unsafe_text = " ".join(suggestions).casefold()
        self.assertNotIn("do i qualify", unsafe_text)
        self.assertNotIn("should i apply", unsafe_text)
        self.assertNotIn("legal advice", unsafe_text)


class Issue14FollowUpApplicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.config_path = self.root / "config" / "provider-config.json"
        self.data_dir = self.root / "data"
        self.embedding_provider = DeterministicEmbeddingProviderFixture()
        save_provider_configuration(self.config_path, provider_configuration("fixture-model-v1"))
        install_minimal_knowledge_release(
            self.data_dir,
            embedding_provider=self.embedding_provider,
        )

    def make_client(self, answer_generator: RecordingGenerator):
        app = create_app(
            config_path=self.config_path,
            data_dir=self.data_dir,
            answer_generator=answer_generator,
            embedding_provider=self.embedding_provider,
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

    def conversation_id_from(self, html: str) -> str:
        match = re.search(r'href="/conversations/([^"]+)"', html)
        self.assertIsNotNone(match, html)
        return match.group(1)

    async def test_suggested_follow_up_button_submits_contextual_turn_with_own_provenance(self):
        generator = RecordingGenerator()
        client = self.make_client(generator)

        first = await self.post_question(
            client,
            "What Danish test do I need for permanent residence?",
        )
        conversation_id = self.conversation_id_from(first.text)

        self.assertEqual(first.status_code, 200)
        self.assertIn("Suggested follow-ups", first.text)
        self.assertIn("What does Prøve i Dansk 2 mean in this context?", first.text)

        follow_up = await self.post_question(
            client,
            "What does Prøve i Dansk 2 mean in this context?",
            conversation_id=conversation_id,
        )

        self.assertEqual(follow_up.status_code, 200)
        self.assertIn("Turn 1", follow_up.text)
        self.assertIn("Turn 2", follow_up.text)
        self.assertIn("fixture-model-v1", follow_up.text)
        self.assertGreaterEqual(follow_up.text.count("Corpus: kr-2026-07-06.1"), 2)
        self.assertGreaterEqual(follow_up.text.count("Evidence Confidence: High"), 2)
        self.assertGreaterEqual(follow_up.text.count("Fresh Tomato Score: High"), 2)
        self.assertIn("previous question", generator.calls[1]["question"].casefold())

        record = ConversationStore(self.data_dir / "conversations.sqlite3").get_conversation(
            conversation_id
        )
        self.assertEqual(len(record["turns"]), 2)
        self.assertEqual(
            record["turns"][1]["question"],
            "What does Prøve i Dansk 2 mean in this context?",
        )
        self.assertEqual(record["turns"][1]["model_identity"]["model"], "fixture-model-v1")
        self.assertEqual(record["turns"][1]["corpus_identity"], "kr-2026-07-06.1")
        self.assertEqual(
            record["turns"][1]["answer"]["citations"][0]["citation_id"],
            "di-rag-doc-permanent-residence-language",
        )

    async def test_failed_follow_up_preserves_prior_turns_and_draft_for_retry(self):
        generator = RecordingGenerator(fail_on_call=2)
        client = self.make_client(generator)

        first = await self.post_question(
            client,
            "What Danish test do I need for permanent residence?",
        )
        conversation_id = self.conversation_id_from(first.text)

        failed = await self.post_question(
            client,
            "What about PD3?",
            conversation_id=conversation_id,
        )

        self.assertEqual(failed.status_code, 422)
        self.assertIn("Answer validation failed", failed.text)
        self.assertIn("What Danish test do I need for permanent residence?", failed.text)
        self.assertIn("What about PD3?", failed.text)
        self.assertIn(f'name="conversation_id" value="{conversation_id}"', failed.text)
        self.assertIn("Retry", failed.text)

        record = ConversationStore(self.data_dir / "conversations.sqlite3").get_conversation(
            conversation_id
        )
        self.assertEqual(len(record["turns"]), 1)
        self.assertEqual(
            record["turns"][0]["question"],
            "What Danish test do I need for permanent residence?",
        )


if __name__ == "__main__":
    unittest.main()
