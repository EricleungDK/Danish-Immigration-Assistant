import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx

from danish_rag.answer_pipeline import (
    AnswerValidationError,
    _answer_messages,
    answer_schema,
    fixed_transport,
)
from danish_rag.social_conversation import TransportSocialReplies, conversation_schema
from danish_rag.conversation_store import ConversationStore
from danish_rag.knowledge_release import install_minimal_knowledge_release
from danish_rag.controlled_model import ControlledModel, GenerationCall
from danish_rag.local_app import create_app
from danish_rag.provider_setup import ProviderConfiguration, save_provider_configuration
from danish_rag.retrieval import HybridRetriever
from danish_rag.local_model_transport import FakeTransport, ModelUnreachableError
from tests.answer_transport import scripted_generator, requests, script, wire
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture
from tests.evaluation_time import fixed_clock


def fixture_draft(call: GenerationCall) -> dict[str, Any]:
    citation_id = call.evidence[0]["citation_id"]
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


def invalid_draft(call: GenerationCall) -> dict[str, Any]:
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


class Issue9AnswerPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_request_is_prompt_grounded_with_the_aliased_schema(self):
        schema = answer_schema(["official-1"])
        generator, transport = scripted_generator({"summary": "Supported.", "sections": []})

        generator.generate(
            question="Where do I register?",
            normalized_question="Where do I register?",
            evidence=[
                {
                    "citation_id": "official-1",
                    "title": "Registration",
                    "publisher": "SIRI",
                    "official_url": "https://example.test/registration",
                    "checked_at_utc": "2026-07-14T00:00:00Z",
                    "content": "Users register directly at the sprogcenter.",
                }
            ],
            configuration=ProviderConfiguration(
                provider_id="ollama",
                endpoint="http://127.0.0.1:11434",
                model="fixture-model",
                provider_version="fixture-provider",
                model_identity={"id": "fixture-model"},
                capabilities=["generation"],
                validated_at_utc="2026-07-14T00:00:00Z",
            ),
            schema=schema,
        )

        request = requests(transport)[0]
        self.assertEqual(request["schema"], answer_schema(["e1"]))
        self.assertIsNone(request["context_tokens"])
        self.assertEqual(request["model"], "fixture-model")
        self.assertIn(
            json.dumps(answer_schema(["e1"]), ensure_ascii=False, sort_keys=True),
            request["messages"][1]["content"],
        )

    async def test_social_conversation_is_structured_and_fact_bounded(self):
        schema = conversation_schema()
        generator, transport = scripted_generator({"response": "Here is a short, harmless joke."})

        result = TransportSocialReplies(fixed_transport(transport)).converse(
            question="Tell me a short joke.",
            configuration=ProviderConfiguration(
                provider_id="ollama",
                endpoint="http://127.0.0.1:11434",
                model="fixture-model",
                provider_version="fixture-provider",
                model_identity={"id": "fixture-model"},
                capabilities=["generation"],
                validated_at_utc="2026-07-14T00:00:00Z",
            ),
            schema=schema,
        )

        self.assertEqual(result, {"response": "Here is a short, harmless joke."})
        request = requests(transport)[0]
        self.assertEqual(request["schema"], schema)
        self.assertIsNone(request["context_tokens"])
        self.assertIn("non-factual social conversation", request["messages"][0]["content"])
        self.assertIn("Do not provide immigration", request["messages"][0]["content"])
        self.assertNotIn("approved_official_evidence", request["messages"][1]["content"])

    async def test_ambiguous_structured_output_is_retried_once(self):
        valid = json.dumps({"summary": "Supported.", "sections": []})
        generator, transport = scripted_generator(valid + "\n" + valid, valid)
        configuration = ProviderConfiguration(
            provider_id="ollama",
            endpoint="http://127.0.0.1:11434",
            model="fixture-model",
            provider_version="fixture-provider",
            model_identity={"id": "fixture-model"},
            capabilities=["generation"],
            validated_at_utc="2026-07-14T00:00:00Z",
        )

        result = generator.generate(
            question="Where do I register?",
            normalized_question="Where do I register?",
            evidence=[],
            configuration=configuration,
            schema=answer_schema([]),
        )

        self.assertEqual(result["summary"], "Supported.")
        self.assertEqual(len(requests(transport)), 2)
        self.assertIn("invalid", requests(transport)[1]["messages"][-1]["content"])

    async def test_a_second_ambiguous_structured_output_is_rejected(self):
        invalid = '{"summary":"first","sections":[]}\n{"summary":"second","sections":[]}'
        generator, transport = scripted_generator(invalid, invalid)

        with self.assertRaises(AnswerValidationError):
            generator.generate(
                question="Where do I register?",
                normalized_question="Where do I register?",
                evidence=[],
                configuration=ProviderConfiguration(
                    provider_id="ollama",
                    endpoint="http://127.0.0.1:11434",
                    model="fixture-model",
                    provider_version="fixture-provider",
                    model_identity={"id": "fixture-model"},
                    capabilities=["generation"],
                    validated_at_utc="2026-07-14T00:00:00Z",
                ),
                schema=answer_schema([]),
            )

        self.assertEqual(len(requests(transport)), 2)

    async def test_generation_contract_answers_supported_parts_without_inventing_absence(self):
        messages = _answer_messages(
            "Where do I register?",
            "Where do I register?",
            [
                {
                    "citation_id": "official-1",
                    "title": "Registration",
                    "publisher": "SIRI",
                    "official_url": "https://example.test/registration",
                    "checked_at_utc": "2026-07-14T00:00:00Z",
                    "content": "Users register directly at the sprogcenter.",
                }
            ],
        )

        system_prompt = messages[0]["content"]
        self.assertIn("exactly as provided", system_prompt)
        self.assertIn("answer every part that the evidence directly supports", system_prompt)
        self.assertIn("Never claim that something is absent", system_prompt)
        self.assertIn("one factual proposition", system_prompt)

    async def test_answer_schema_requires_nonempty_summary_and_section_text(self):
        schema = answer_schema(["official-1"])

        self.assertEqual(schema["properties"]["summary"]["minLength"], 1)
        section_properties = schema["properties"]["sections"]["items"]["properties"]
        self.assertEqual(section_properties["text"]["minLength"], 1)
        self.assertEqual(
            section_properties["citation_ids"]["items"]["enum"],
            ["official-1"],
        )

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.config_path = root / "config" / "provider-config.json"
        self.data_dir = root / "data"
        self.embedding_provider = DeterministicEmbeddingProviderFixture()
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

    def make_client(self, model: ControlledModel):
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

    def make_transport_client(self, transport):
        app = create_app(
            config_path=self.config_path,
            data_dir=self.data_dir,
            transport=transport,
            embedding_provider=self.embedding_provider, clock=fixed_clock,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)
        return client

    async def test_application_answers_through_the_injected_transport_with_model_verification(self):
        from danish_rag.answer_verification import source_witnesses

        install_minimal_knowledge_release(
            self.data_dir, embedding_provider=self.embedding_provider, clock=fixed_clock,
        )
        question = "What Danish test do I need for permanent residence?"
        evidence = HybridRetriever.from_data_dir(
            self.data_dir, embedding_provider=self.embedding_provider, clock=fixed_clock,
        ).retrieve(question)
        claim = source_witnesses(evidence)["e1s1"]["quote"]
        proposed = {"summary": "Introduction.", "sections": [
            {"kind": "official_fact", "text": claim, "citation_ids": [evidence[0]["citation_id"]]}]}
        supported = {"verdict": "supported", "failure_reason": "none",
                     "witnesses": [{"quote_id": "e1s1"}]}
        transport = script(
            FakeTransport(), wire(proposed, {evidence[0]["citation_id"]: "e1"}),
            {"checks": {"item_0": supported, "item_1": supported}})
        client = self.make_transport_client(transport)

        response = await client.post(
            "/ask", data={"question": question}, headers={"Origin": "http://testserver"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Evidence Confidence: High", response.text)
        self.assertIn("Fresh Tomato Score: High", response.text)
        self.assertIn("SIRI", response.text)
        sent = requests(transport)
        self.assertEqual([item["context_tokens"] for item in sent], [None, 8192])
        self.assertEqual(sent[0]["model"], "fixture-model")
        record = ConversationStore(self.data_dir / "conversations.sqlite3")
        saved = record.get_conversation(record.list_conversations()[0]["id"])
        self.assertEqual(saved["answer"]["sections"][0]["text"], claim)
        self.assertEqual(saved["answer"]["summary"], claim)

    async def test_transport_failure_through_the_application_is_a_safe_provider_error(self):
        transport = FakeTransport()
        transport.queue_error(ModelUnreachableError("refused"))
        client = self.make_transport_client(transport)

        response = await client.post(
            "/ask",
            data={"question": "What Danish test do I need for permanent residence?"},
            headers={"Origin": "http://testserver"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Local generation provider is unavailable", response.text)
        self.assertEqual(
            ConversationStore(self.data_dir / "conversations.sqlite3").list_conversations(), [])

    async def test_minimal_release_installs_and_hybrid_retrieval_finds_supported_source(self):
        installation = install_minimal_knowledge_release(
            self.data_dir,
            embedding_provider=self.embedding_provider, clock=fixed_clock,
        )

        self.assertEqual(installation["manifest"]["knowledge_release_id"], "kr-2026-07-06.1")
        self.assertEqual(installation["index"]["retrieval"], "hybrid")
        self.assertEqual(installation["index"]["lexical_engine"], "sqlite-fts5")
        self.assertEqual(installation["index"]["dense_engine"], "local-dense-json")
        self.assertEqual(installation["index"]["embedding_model"], "embeddinggemma")
        self.assertEqual(installation["index"]["corpus_identity"], installation["manifest"]["corpus_id"])

        retriever = HybridRetriever.from_data_dir(
            self.data_dir,
            embedding_provider=self.embedding_provider, clock=fixed_clock,
        )
        results = retriever.retrieve("What Danish test do I need for permanent residence?")

        self.assertTrue(results)
        self.assertEqual(
            results[0]["document_id"],
            "di-rag-doc-permanent-residence-language",
        )
        self.assertEqual(results[0]["publisher"], "SIRI")
        self.assertIn("Prøve i Dansk 2", results[0]["content"])

    async def test_supported_question_renders_cited_answer_and_persists_record(self):
        model = ControlledModel(draft=fixture_draft)
        client = self.make_client(model)

        response = await client.post(
            "/ask",
            data={"question": "What Danish test do I need for permanent residence?"},
            headers={"Origin": "http://testserver"},
        )

        self.assertEqual(response.status_code, 200)
        html = response.text
        call = model.calls[0]
        self.assertEqual(call.model, "fixture-model")
        self.assertIn(
            "Permanent residence language requirements",
            [item["title"] for item in call.evidence],
        )
        citation_schema = call.schema["properties"]["sections"][
            "items"
        ]["properties"]["citation_ids"]["items"]
        self.assertEqual(
            sorted(alias for alias in citation_schema["enum"] if alias.startswith("e")),
            sorted(item["citation_id"] for item in call.evidence),
        )
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
                app=create_app(
                    config_path=self.config_path,
                    data_dir=self.data_dir,
                    embedding_provider=self.embedding_provider, clock=fixed_clock,
                )
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
        model = ControlledModel(draft=invalid_draft)
        client = self.make_client(model)

        response = await client.post(
            "/ask",
            data={"question": "What Danish test do I need for permanent residence?"},
            headers={"Origin": "http://testserver"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("answer validation failed; no answer was saved", response.text)
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
