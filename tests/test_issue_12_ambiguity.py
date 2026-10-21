import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx

from danish_rag.answer_pipeline import answer_schema, fixed_transport
from danish_rag.conversation_store import ConversationStore
from danish_rag.evaluation_quality_bar import load_evaluation_cases
from danish_rag.knowledge_release import install_minimal_knowledge_release
from danish_rag.controlled_model import ControlledModel, GenerationCall
from danish_rag.local_app import create_app
from danish_rag.local_model_transport import FakeTransport
from danish_rag.provider_setup import ProviderConfiguration, save_provider_configuration
from danish_rag.social_conversation import (
    SocialConversation, TransportSocialReplies, classify_conversation_turn, conversation_schema,
)
from tests.answer_transport import lexical_service
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture
from tests.evaluation_time import fixed_clock


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


def social_reply(model, question, turns=None):
    """The application's routing for a non-domain turn: classify, then social conversation."""

    kind = classify_conversation_turn(question, conversation_turns=turns)
    assert kind in ("greeting", "social"), kind
    return SocialConversation(
        TransportSocialReplies(fixed_transport(FakeTransport(responder=model)))).reply(
        question, provider_configuration(), kind=kind, corpus_identity="fixture-corpus")


JOKE = "Why did the tomato blush? It saw the salad dressing."


def fixture_draft(call: GenerationCall) -> dict[str, Any]:
    citation_id = call.evidence[0]["citation_id"]
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


def controlled_model(conversation_response: str = JOKE) -> ControlledModel:
    return ControlledModel(draft=fixture_draft, conversation_response=conversation_response)


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
                model = controlled_model()

                result = lexical_service(FakeTransport(responder=model), retriever).answer(
                    question,
                    provider_configuration(),
                )

                self.assertEqual(result.answer["response_kind"], "clarification")
                self.assertIn("Which application", result.answer["summary"])
                self.assertIn("application purpose", result.answer["sections"][0]["text"])
                self.assertEqual(retriever.calls, [])
                self.assertEqual(model.calls, [])

    def test_clarification_turn_trust_indicators_are_unchanged(self):
        result = lexical_service(
            FakeTransport(responder=controlled_model()), CountingRetriever()
        ).answer("Which Danish test do I need for my application?", provider_configuration())

        self.assertEqual(
            result.answer["trust"],
            {
                "evidence_confidence": "Low",
                "evidence_confidence_reason": (
                    "No substantive answer was generated because the missing context can "
                    "change the official answer."
                ),
                "fresh_tomato_score": "Low",
                "fresh_tomato_reason": (
                    "No material source has been attached to this clarification turn."
                ),
            },
        )

    def test_greeting_is_answered_locally(self):
        model = controlled_model()

        result = social_reply(model, "hi")

        self.assertEqual(result.answer["response_kind"], "conversation")
        self.assertIs(result.answer["generation_used"], False)
        self.assertIn("Hello", result.answer["summary"])
        self.assertEqual(result.answer["citations"], [])
        self.assertEqual(model.conversation_calls, [])

    def test_non_factual_social_turn_uses_local_model(self):
        model = controlled_model()

        result = social_reply(model, "Tell me a short joke.")

        self.assertEqual(result.answer["response_kind"], "conversation")
        self.assertIs(result.answer["generation_used"], True)
        self.assertEqual(
            result.answer["summary"],
            "Why did the tomato blush? It saw the salad dressing.",
        )
        self.assertEqual(result.answer["citations"], [])
        self.assertEqual(
            [(call.question, call.schema) for call in model.conversation_calls],
            [("Tell me a short joke.", conversation_schema())],
        )

    def test_unrelated_factual_turn_uses_bounded_model_mode(self):
        model = controlled_model(
            conversation_response=(
                "I can chat briefly, but factual answers are limited to the installed "
                "corpus of approved official sources."
            )
        )

        result = social_reply(
            model,
            "What is the capital of France?",
            [{"question": "What is PD2?", "answer": {"response_kind": "answer"}}],
        )

        self.assertEqual(result.answer["response_kind"], "conversation")
        self.assertIs(result.answer["generation_used"], True)
        self.assertIn("factual answers are limited", result.answer["summary"])
        self.assertEqual(result.answer["citations"], [])
        self.assertEqual(len(model.conversation_calls), 1)

    async def test_greeting_renders_as_conversation_without_evidence_indicators(self):
        model = controlled_model()
        client = self.make_client(model)

        response = await self.post_question(client, "hi")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Conversation", response.text)
        self.assertIn("Handled locally without retrieval or model generation", response.text)
        self.assertNotIn("Evidence Confidence", response.text)
        self.assertEqual(model.calls, [])
        self.assertEqual(model.conversation_calls, [])

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
        model = controlled_model()

        result = lexical_service(FakeTransport(responder=model), retriever).answer(
            "What is PD3?",
            provider_configuration(),
        )

        self.assertEqual(result.answer["response_kind"], "answer")
        self.assertEqual(
            result.answer["assumptions"],
            [
                (
                    "I am explaining the examination term generally and, where the "
                    "sources support it, its relevance to permanent-residence language "
                    "requirements. This is not a personal eligibility decision."
                )
            ],
        )
        self.assertEqual(len(retriever.calls), 1)
        self.assertIn(result.answer["assumptions"][0], retriever.calls[0])
        self.assertEqual(model.calls[0].question, retriever.calls[0])
        self.assertEqual(
            model.calls[0].schema,
            answer_schema(["e1"]),
        )

    def test_definition_does_not_override_consequential_application_ambiguity(self):
        retriever = CountingRetriever()
        model = controlled_model()
        result = lexical_service(FakeTransport(responder=model), retriever).answer(
            "What is PD3 for permanent residence or citizenship?",
            provider_configuration(),
        )
        self.assertEqual(result.answer["response_kind"], "clarification")
        self.assertEqual(retriever.calls, [])
        self.assertEqual(model.calls, [])

    def test_explicit_registration_definition_does_not_gain_residence_context(self):
        retriever = CountingRetriever()
        model = controlled_model()
        question = "What is PD3 registration?"
        result = lexical_service(FakeTransport(responder=model), retriever).answer(
            question, provider_configuration(),
        )
        self.assertEqual(result.answer["response_kind"], "answer")
        self.assertEqual(retriever.calls, [question])
        self.assertEqual(model.calls[0].question, question)

    def test_application_specific_definition_without_purpose_needs_clarification(self):
        for question in (
            "What does PD3 mean for my application?",
            "What does Studieprøven mean for my application?",
            "What does Studieproven mean for my application?",
            "What is PD3 for permanent-residence or citizenship?",
        ):
            with self.subTest(question=question):
                retriever = CountingRetriever()
                model = controlled_model()
                result = lexical_service(FakeTransport(responder=model), retriever).answer(
                    question, provider_configuration(),
                )
                self.assertEqual(result.answer["response_kind"], "clarification")
                self.assertEqual(retriever.calls, [])
                self.assertEqual(model.calls, [])

    def test_other_explicit_definition_purpose_does_not_gain_residence_context(self):
        for question in (
            "What is PD3 for university admission?",
            "What does PD3 mean in professional accreditation?",
            "What does PD3 cost?",
        ):
            with self.subTest(question=question):
                retriever = CountingRetriever()
                model = controlled_model()
                result = lexical_service(FakeTransport(responder=model), retriever).answer(
                    question, provider_configuration(),
                )
                self.assertEqual(result.answer["response_kind"], "answer")
                self.assertEqual(retriever.calls, [question])
                self.assertEqual(model.calls[0].question, question)

    async def test_clarification_turn_stays_in_conversation_and_feeds_next_question(self):
        model = controlled_model()
        client = self.make_client(model)

        first = await self.post_question(
            client,
            "Which Danish test do I need for my application?",
        )

        self.assertEqual(first.status_code, 200)
        self.assertIn("Clarification needed", first.text)
        self.assertIn("Which application", first.text)
        self.assertEqual(model.calls, [])
        conversation_id = self.conversation_id_from(first.text)

        second = await self.post_question(
            client,
            "permanent residence",
            conversation_id=conversation_id,
        )

        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(model.calls), 1)
        normalized = model.calls[0].normalized_question.casefold()
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

    def make_client(self, model: ControlledModel):
        install_minimal_knowledge_release(
            self.data_dir,
            embedding_provider=self.embedding_provider, clock=fixed_clock,
        )
        app = create_app(
            config_path=self.config_path,
            data_dir=self.data_dir,
            transport=FakeTransport(responder=model),
            embedding_provider=self.embedding_provider, clock=fixed_clock,
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
