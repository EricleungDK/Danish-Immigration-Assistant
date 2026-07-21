import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx

from danish_rag.answer_pipeline import AnswerService, answer_schema
from danish_rag.conversation_store import ConversationStore
from danish_rag.evaluation_quality_bar import load_evaluation_cases
from danish_rag.knowledge_release import install_minimal_knowledge_release
from danish_rag.local_app import create_app
from danish_rag.provider_setup import ProviderConfiguration, save_provider_configuration
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture


ROOT = Path(__file__).resolve().parents[1]


def provider_configuration() -> ProviderConfiguration:
    return ProviderConfiguration(
        provider_id="openai_compatible",
        endpoint="http://127.0.0.1:1234",
        model="fixture-model",
        provider_version="fixture-provider",
        model_identity={"id": "fixture-model"},
        capabilities=["generation"],
        validated_at_utc="2026-07-06T12:00:00+00:00",
    )


class CountingRetriever:
    manifest = {"corpus_id": "kr-fixture"}

    def __init__(self) -> None:
        self.calls: list[str] = []

    def retrieve(self, question: str) -> list[dict[str, Any]]:
        self.calls.append(question)
        return [
            {
                "citation_id": "di-rag-doc-permanent-residence-language",
                "document_id": "di-rag-doc-permanent-residence-language",
                "source_id": "nyidanmark-permanent-residence-language-requirements",
                "title": "Permanent residence language requirements",
                "publisher": "SIRI",
                "official_url": "https://www.nyidanmark.dk/da/Du-vil-ansoege/Permanent-ophold",
                "checked_at_utc": "2026-06-15T09:00:00Z",
                "knowledge_release_id": "kr-fixture",
                "corpus_identity": "kr-fixture",
                "review_state": "approved-current",
                "source_health": "healthy",
                "agreement_state": "supports",
                "content": (
                    "Permanent opholdstilladelse kan kræve dokumentation for "
                    "bestået Prøve i Dansk 2 eller en tilsvarende danskprøve."
                ),
            }
        ]


class FixtureAnswerGenerator:
    def __init__(
        self,
        *,
        conversation_response: str = (
            "Why did the tomato blush? It saw the salad dressing."
        ),
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.conversation_calls: list[dict[str, Any]] = []
        self.conversation_response = conversation_response

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
                "schema": schema,
            }
        )
        citation_id = evidence[0]["citation_id"]
        return {
            "summary": "The official source supports a permanent-residence language answer.",
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

    def converse(
        self,
        *,
        question: str,
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.conversation_calls.append(
            {
                "question": question,
                "schema": schema,
            }
        )
        return {"response": self.conversation_response}


class Issue12AmbiguityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.config_path = self.root / "config" / "provider-config.json"
        self.data_dir = self.root / "data"
        self.embedding_provider = DeterministicEmbeddingProviderFixture()
        save_provider_configuration(self.config_path, provider_configuration())

    def test_consequential_ambiguity_requests_clarification_before_retrieval(self):
        questions = [
            "Which Danish test do I need for my application?",
            "Which Danish test do I need for permanent residence or citizenship?",
            "Do I need Prøve i Dansk 3 for registration or for an application requirement?",
        ]
        for question in questions:
            with self.subTest(question=question):
                retriever = CountingRetriever()
                generator = FixtureAnswerGenerator()

                result = AnswerService(retriever=retriever, generator=generator).answer(
                    question,
                    provider_configuration(),
                )

                self.assertEqual(result.answer["response_kind"], "clarification")
                self.assertIn("Which application", result.answer["summary"])
                self.assertIn("application purpose", result.answer["sections"][0]["text"])
                self.assertEqual(retriever.calls, [])
                self.assertEqual(generator.calls, [])

    def test_greeting_bypasses_retrieval_and_generation(self):
        retriever = CountingRetriever()
        generator = FixtureAnswerGenerator()

        result = AnswerService(retriever=retriever, generator=generator).answer(
            "hi",
            provider_configuration(),
        )

        self.assertEqual(result.answer["response_kind"], "conversation")
        self.assertIs(result.answer["generation_used"], False)
        self.assertIn("Hello", result.answer["summary"])
        self.assertEqual(result.answer["citations"], [])
        self.assertEqual(retriever.calls, [])
        self.assertEqual(generator.calls, [])
        self.assertEqual(generator.conversation_calls, [])

    def test_non_factual_social_turn_uses_local_model_without_retrieval(self):
        retriever = CountingRetriever()
        generator = FixtureAnswerGenerator()

        result = AnswerService(retriever=retriever, generator=generator).answer(
            "Tell me a short joke.",
            provider_configuration(),
        )

        self.assertEqual(result.answer["response_kind"], "conversation")
        self.assertIs(result.answer["generation_used"], True)
        self.assertEqual(
            result.answer["summary"],
            "Why did the tomato blush? It saw the salad dressing.",
        )
        self.assertEqual(result.answer["citations"], [])
        self.assertEqual(retriever.calls, [])
        self.assertEqual(generator.calls, [])
        self.assertEqual(
            generator.conversation_calls,
            [
                {
                    "question": "Tell me a short joke.",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "response": {"type": "string", "minLength": 1},
                        },
                        "required": ["response"],
                        "additionalProperties": False,
                    },
                }
            ],
        )

    def test_unrelated_factual_turn_uses_bounded_model_mode_without_retrieval(self):
        retriever = CountingRetriever()
        generator = FixtureAnswerGenerator(
            conversation_response=(
                "I can chat briefly, but factual answers are limited to the installed "
                "corpus of approved official sources."
            )
        )

        result = AnswerService(retriever=retriever, generator=generator).answer(
            "What is the capital of France?",
            provider_configuration(),
            conversation_turns=[
                {
                    "question": "What is PD2?",
                    "answer": {"response_kind": "answer"},
                }
            ],
        )

        self.assertEqual(result.answer["response_kind"], "conversation")
        self.assertIs(result.answer["generation_used"], True)
        self.assertIn("factual answers are limited", result.answer["summary"])
        self.assertEqual(result.answer["citations"], [])
        self.assertEqual(retriever.calls, [])
        self.assertEqual(generator.calls, [])
        self.assertEqual(len(generator.conversation_calls), 1)

    async def test_greeting_renders_as_conversation_without_evidence_indicators(self):
        generator = FixtureAnswerGenerator()
        client = self.make_client(generator)

        response = await self.post_question(client, "hi")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Conversation", response.text)
        self.assertIn("Handled locally without retrieval or model generation", response.text)
        self.assertNotIn("Evidence Confidence", response.text)
        self.assertEqual(generator.calls, [])
        self.assertEqual(generator.conversation_calls, [])

        conversations = ConversationStore(
            self.data_dir / "conversations.sqlite3"
        ).list_conversations()
        self.assertEqual(len(conversations), 1)
        record = ConversationStore(
            self.data_dir / "conversations.sqlite3"
        ).get_conversation(conversations[0]["id"])
        self.assertEqual(record["answer"]["response_kind"], "conversation")
        self.assertIs(record["answer"]["generation_used"], False)

    def test_low_risk_ambiguity_answers_with_visible_assumption(self):
        retriever = CountingRetriever()
        generator = FixtureAnswerGenerator()

        result = AnswerService(retriever=retriever, generator=generator).answer(
            "What is PD3?",
            provider_configuration(),
        )

        self.assertEqual(result.answer["response_kind"], "answer")
        self.assertEqual(
            result.answer["assumptions"],
            [
                (
                    "You are asking for a general explanation of the Danish examination "
                    "term, not a personal eligibility decision."
                )
            ],
        )
        self.assertEqual(retriever.calls, ["What is PD3?"])
        self.assertEqual(
            generator.calls[0]["schema"],
            answer_schema(["di-rag-doc-permanent-residence-language"]),
        )

    async def test_clarification_turn_stays_in_conversation_and_feeds_next_question(self):
        generator = FixtureAnswerGenerator()
        client = self.make_client(generator)

        first = await self.post_question(
            client,
            "Which Danish test do I need for my application?",
        )

        self.assertEqual(first.status_code, 200)
        self.assertIn("Clarification needed", first.text)
        self.assertIn("Which application", first.text)
        self.assertEqual(generator.calls, [])
        conversation_id = self.conversation_id_from(first.text)

        second = await self.post_question(
            client,
            "permanent residence",
            conversation_id=conversation_id,
        )

        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(generator.calls), 1)
        normalized = generator.calls[0]["normalized_question"].casefold()
        self.assertIn("which danish test do i need for my application", normalized)
        self.assertIn("permanent residence", normalized)
        self.assertIn("Turn 1", second.text)
        self.assertIn("Turn 2", second.text)
        self.assertIn("Clarification needed", second.text)
        self.assertIn("Official fact", second.text)

        record = ConversationStore(self.data_dir / "conversations.sqlite3").get_conversation(
            conversation_id
        )
        self.assertEqual(record["turns"][0]["answer"]["response_kind"], "clarification")
        self.assertEqual(record["turns"][1]["question"], "permanent residence")
        self.assertIn(
            "Which Danish test do I need for my application?",
            record["turns"][1]["normalized_question"],
        )

    def test_evaluation_cases_distinguish_clarify_answer_and_refuse_outcomes(self):
        cases = load_evaluation_cases(ROOT / "data/evaluation/evaluation-set-v0.1-candidate.json")

        expected_behaviors = {
            case["final_answer_expectations"]["expected_behavior"]
            for case in cases["cases"]
        }

        self.assertTrue({"answer", "clarify", "refuse"}.issubset(expected_behaviors))

    def make_client(self, answer_generator: FixtureAnswerGenerator):
        install_minimal_knowledge_release(
            self.data_dir,
            embedding_provider=self.embedding_provider,
        )
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


if __name__ == "__main__":
    unittest.main()
