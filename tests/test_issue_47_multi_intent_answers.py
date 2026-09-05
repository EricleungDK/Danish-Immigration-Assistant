import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx

from danish_rag.local_app import create_app
from danish_rag.provider_setup import (
    ProviderConfiguration,
    save_provider_configuration,
)
from danish_rag.retrieval import HybridRetriever, build_hybrid_index
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture


PERMANENT_RESIDENCE_DOCUMENT_ID = "di-rag-doc-permanent-residence-language"
REGISTRATION_DOCUMENT_ID = "di-rag-doc-registration-deadlines-2026"


def eligible_document(
    document_id: str,
    *,
    content: str,
    topic_tags: list[str],
) -> dict[str, Any]:
    return {
        "approval_state": "approved",
        "checked_at_utc": "2026-07-29T12:00:00+00:00",
        "content": content,
        "content_origin": "project-authored-fixture",
        "document_id": document_id,
        "english_search_terms": content,
        "final_url": f"https://example.test/{document_id}",
        "language": "da",
        "official_url": f"https://example.test/{document_id}",
        "publisher": "Test publisher",
        "review_state": "approved-current",
        "source_health": "healthy",
        "source_id": f"source-{document_id}",
        "title": document_id,
        "topic_tags": topic_tags,
    }


class MultiIntentAnswerGeneratorFixture:
    """Deterministic fixture for the external generation-provider boundary."""

    def __init__(self) -> None:
        self.evidence_ids: list[str] = []

    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        del question, normalized_question, configuration, schema
        self.evidence_ids = [str(item["document_id"]) for item in evidence]
        citation_ids = {
            str(item["document_id"]): str(item["citation_id"])
            for item in evidence
        }
        return {
            "summary": (
                "The approved sources address both the permanent-residence "
                "requirement and test registration."
            ),
            "sections": [
                {
                    "kind": "official_fact",
                    "text": (
                        "For permanent opholdstilladelse, the applicant must pass "
                        "Danish language test 2 (Prøve i Dansk 2), or a Danish exam "
                        "of an equivalent or higher level."
                    ),
                    "citation_ids": [
                        citation_ids[PERMANENT_RESIDENCE_DOCUMENT_ID]
                    ],
                },
                {
                    "kind": "official_fact",
                    "text": (
                        "Users register for a test directly at the sprogcenter where "
                        "they want to take the test."
                    ),
                    "citation_ids": [citation_ids[REGISTRATION_DOCUMENT_ID]],
                },
            ],
        }


class Issue47MultiIntentAnswerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from tests.source_freshness_fixture import fixture_review_time

        self.enterContext(fixture_review_time())
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
                validated_at_utc="2026-07-29T12:00:00+00:00",
            ),
        )

    def test_result_limit_keeps_evidence_for_each_detected_intent(self):
        manifest = {
            "corpus_id": "issue-47-ranking-pressure-corpus",
            "knowledge_release_id": "issue-47-ranking-pressure-release",
        }
        permanent_residence_content = (
            "PD2 proves the permanent residence Danish language requirement. "
            "Prøve i Dansk 2 permanent ophold permanent opholdstilladelse."
        )
        documents = [
            eligible_document(
                f"{PERMANENT_RESIDENCE_DOCUMENT_ID}-{candidate_number:02d}",
                content=permanent_residence_content,
                topic_tags=["permanent-residence", "language-requirement"],
            )
            for candidate_number in range(21)
        ]
        documents.append(
            eligible_document(
                REGISTRATION_DOCUMENT_ID,
                content="Enrollment details.",
                topic_tags=["registration-logistics", "language-requirement"],
            )
        )
        build_hybrid_index(
            self.data_dir,
            documents,
            manifest=manifest,
            embedding_provider=self.embedding_provider,
        )
        dense_index = json.loads(
            (
                self.data_dir
                / "index"
                / manifest["knowledge_release_id"]
                / "dense-index.json"
            ).read_text(encoding="utf-8")
        )
        retriever = HybridRetriever(
            data_dir=self.data_dir,
            active_release={"manifest": manifest},
            documents=documents,
            dense_index=dense_index,
            embedding_provider=self.embedding_provider,
        )

        results = retriever.retrieve(
            (
                "What does PD2 prove for permanent residence, and where do I "
                "register for PD3?"
            ),
            limit=3,
        )

        self.assertEqual(len(results), 3)
        self.assertIn(
            REGISTRATION_DOCUMENT_ID,
            {result["document_id"] for result in results},
        )

    async def test_combined_question_answers_both_intents_from_eligible_evidence(self):
        generator = MultiIntentAnswerGeneratorFixture()
        app = create_app(
            config_path=self.config_path,
            data_dir=self.data_dir,
            answer_generator=generator,
            embedding_provider=self.embedding_provider,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)

        response = await client.post(
            "/ask",
            data={
                "question": (
                    "What does PD2 prove for permanent residence, and where do I "
                    "register for PD3?"
                )
            },
            headers={"Origin": "http://testserver"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            {
                PERMANENT_RESIDENCE_DOCUMENT_ID,
                REGISTRATION_DOCUMENT_ID,
            }.issubset(set(generator.evidence_ids)),
            generator.evidence_ids,
        )
        self.assertIn("Prøve i Dansk 2", response.text)
        self.assertIn("register for a test directly at the sprogcenter", response.text)
        self.assertIn("Permanent residence language requirements", response.text)
        self.assertIn("Tilmeldingsfrister og prøvedatoer", response.text)


if __name__ == "__main__":
    unittest.main()
