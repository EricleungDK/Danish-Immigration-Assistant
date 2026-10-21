"""The controlled model: one deterministic stand-in served through the shared fake (#77)."""

import unittest

from danish_rag.answer_pipeline import (
    answer_schema,
    AnswerService, AnswerValidationError, LexicalAnswerVerifier, ModelAnswerVerifier,
    TransportAnswerGenerator, fixed_transport,
)
from danish_rag.controlled_model import CannedAnswer, ControlledModel
from danish_rag.embedding_provider import OllamaEmbeddingProvider
from danish_rag.local_model_transport import (
    FakeTransport, FakeTransportScriptError, ModelUnreachableError, UnsupportedOperationError,
)
from danish_rag.social_conversation import TransportSocialReplies, conversation_schema
from tests.evaluation_time import fixed_clock
from tests.test_issue_14_followups import RecordingRetriever, provider_configuration

FACT = (
    "Permanent opholdstilladelse can require documentation for bestået "
    "Prøve i Dansk 2 or an equivalent Danish test."
)


def supported_draft(call):
    return {
        "summary": "The official source supports a language-requirement answer.",
        "sections": [{
            "kind": "official_fact", "text": FACT,
            "citation_ids": [call.evidence[0]["citation_id"]],
        }],
    }


def service(model, *, verifier=None):
    source = fixed_transport(FakeTransport(responder=model))
    return AnswerService(
        retriever=RecordingRetriever(),
        generator=TransportAnswerGenerator(source),
        verifier=verifier or ModelAnswerVerifier(source),
        clock=fixed_clock,
    )


class ControlledModelTests(unittest.TestCase):
    def test_writes_the_given_draft_and_verifies_it_over_the_model_regime(self):
        model = ControlledModel(draft=supported_draft)

        result = service(model).answer(
            "What Danish test do I need for permanent residence?", provider_configuration())

        self.assertEqual(result.answer["citations"][0]["citation_id"], "language-source")
        self.assertEqual(result.answer["sections"][0]["text"], FACT)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(model.verifications), 1)
        call = model.calls[0]
        self.assertEqual(call.model, "fixture-model")
        self.assertEqual(call.evidence[0]["citation_id"], "e1")
        self.assertEqual(call.evidence[0]["content"], FACT)
        self.assertIn("permanent residence", call.question)
        self.assertFalse(call.repair)

    def test_the_verifier_is_honest_an_unsupported_claim_fails_closed(self):
        def unsupported(call):
            return {
                "summary": "Claims beyond the source.",
                "sections": [{
                    "kind": "official_fact",
                    "text": "Citizens receive a free flight to Copenhagen.",
                    "citation_ids": [call.evidence[0]["citation_id"]],
                }],
            }

        with self.assertRaises(AnswerValidationError):
            service(ControlledModel(draft=unsupported)).answer(
                "What Danish test do I need for permanent residence?", provider_configuration())

    def test_a_rejected_draft_is_repaired_with_a_marked_second_generation(self):
        seen = []

        def draft(call):
            seen.append(call.repair)
            if not call.repair:
                return {"summary": "Bad.", "sections": [{
                    "kind": "official_fact", "text": "Unrelated invented claim.",
                    "citation_ids": [call.evidence[0]["citation_id"]]}]}
            return supported_draft(call)

        model = ControlledModel(draft=draft)
        result = service(model).answer("What Danish test do I need for permanent residence?", provider_configuration())

        self.assertEqual(seen, [False, True])
        self.assertEqual(result.answer["sections"][0]["text"], FACT)

    def test_serves_lexical_services_without_any_verification_request(self):
        model = ControlledModel(draft=supported_draft)

        service(model, verifier=LexicalAnswerVerifier()).answer(
            "What Danish test do I need for permanent residence?", provider_configuration())

        self.assertEqual(len(model.calls), 1)
        self.assertEqual(model.verifications, [])

    def test_social_replies_are_configured_text_and_recorded(self):
        model = ControlledModel(conversation_response="Why did the tomato blush?")
        replies = TransportSocialReplies(fixed_transport(FakeTransport(responder=model)))

        reply = replies.converse(
            question="Tell me a joke", configuration=provider_configuration(),
            schema=conversation_schema())

        self.assertEqual(reply, {"response": "Why did the tomato blush?"})
        self.assertEqual(model.conversation_calls[0].question, "Tell me a joke")

    def test_a_draft_may_raise_a_transport_error_to_model_an_outage(self):
        def down(call):
            raise ModelUnreachableError("provider down")

        with self.assertRaises(Exception) as caught:
            service(ControlledModel(draft=down)).answer(
                "What Danish test do I need for permanent residence?", provider_configuration())
        self.assertIn("unavailable", str(caught.exception))

    def test_generation_without_a_draft_fails_loudly(self):
        with self.assertRaises(FakeTransportScriptError):
            service(ControlledModel()).answer(
                "What Danish test do I need for permanent residence?", provider_configuration())

    def test_embeddings_are_deterministic_unit_vectors_with_a_stable_identity(self):
        provider = OllamaEmbeddingProvider(
            transport=FakeTransport(responder=ControlledModel(embedding_dimensions=16)))

        first = provider.embed("embeddinggemma", "Prøve i Dansk 2")

        self.assertEqual(first, provider.embed("embeddinggemma", "Prøve i Dansk 2"))
        self.assertEqual(len(first), 16)
        self.assertAlmostEqual(sum(v * v for v in first), 1.0, places=6)
        identity = provider.inspect_model("embeddinggemma")
        self.assertEqual(identity["model"], "embeddinggemma")
        self.assertTrue(identity["digest"])

    def test_operations_it_does_not_model_are_unsupported_not_silent(self):
        fake = FakeTransport(responder=ControlledModel())
        with self.assertRaises(UnsupportedOperationError):
            fake.version()


class CannedAnswerTests(unittest.TestCase):
    def evidence(self):
        return [
            {**RecordingRetriever().retrieve("q")[0], "citation_id": "doc-a", "content": FACT},
            {**RecordingRetriever().retrieve("q")[0], "citation_id": "doc-b",
             "content": "Registration is done at the sprogcenter."},
        ]

    def payload(self, *cited):
        return {"summary": "Canned.", "sections": [
            {"kind": "official_fact", "text": FACT, "citation_ids": list(cited)}]}

    def test_a_payload_written_with_the_retrievers_ids_reaches_the_model_as_aliases(self):
        sources = self.evidence()
        canned = CannedAnswer(self.payload("doc-b"), sources)
        model = ControlledModel(draft=canned)
        generator = TransportAnswerGenerator(fixed_transport(FakeTransport(responder=model)))

        result = generator.generate(
            question="q", normalized_question="q", evidence=sources,
            configuration=provider_configuration(), schema=answer_schema(["doc-a", "doc-b"]))

        self.assertEqual(result["sections"][0]["citation_ids"], ["doc-b"])
        self.assertEqual(canned.calls[0]["evidence_ids"], ["doc-a", "doc-b"])
        self.assertEqual(canned.calls[0]["question"], "q")
        self.assertEqual(canned.calls[0]["schema"], model.calls[0].schema)
        self.assertEqual(sorted(canned.calls[0]["schema_ids"]), ["doc-a", "doc-b"])

    def test_sources_may_be_resolved_per_question(self):
        sources = self.evidence()
        canned = CannedAnswer(self.payload("doc-a"), lambda question: sources)
        model = ControlledModel(draft=canned)
        generator = TransportAnswerGenerator(fixed_transport(FakeTransport(responder=model)))

        result = generator.generate(
            question="q", normalized_question="q", evidence=sources[:1],
            configuration=provider_configuration(), schema=answer_schema(["doc-a"]))

        self.assertEqual(result["sections"][0]["citation_ids"], ["doc-a"])
        self.assertEqual(canned.calls[0]["evidence_ids"], ["doc-a"])

    def test_an_unknown_shown_source_fails_loudly(self):
        canned = CannedAnswer(self.payload("doc-a"), self.evidence()[:1])
        generator = TransportAnswerGenerator(fixed_transport(
            FakeTransport(responder=ControlledModel(draft=canned))))

        with self.assertRaises(KeyError):
            generator.generate(
                question="q", normalized_question="q", evidence=self.evidence(),
                configuration=provider_configuration(),
                schema=answer_schema(["doc-a", "doc-b"]))


if __name__ == "__main__":
    unittest.main()
