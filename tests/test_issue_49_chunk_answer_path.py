import tempfile
import unittest
from pathlib import Path
from typing import Any

from danish_rag.answer_pipeline import AnswerService
from danish_rag.conversation_store import ConversationStore
from danish_rag.knowledge_release import install_knowledge_release
from danish_rag.provider_setup import ProviderConfiguration
from danish_rag.retrieval import HybridRetriever
from tests.chunk_release_fixture import (
    build_chunked_release_fixture,
    bundled_reviewed_source_and_document,
)
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture
from tests.release_trust_fixture import create_test_release_trust_fixture


class ChunkCitationGenerator:
    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        selected = next(
            item for item in evidence if "equivalent or higher" in item["content"]
        )
        return {
            "summary": "The reviewed source describes equivalent Danish tests.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": selected["content"],
                    "citation_ids": [selected["citation_id"]],
                }
            ],
        }


class ChunkAnswerPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.data_dir = self.root / "data"
        self.embedding_provider = DeterministicEmbeddingProviderFixture()
        self.release_trust = create_test_release_trust_fixture(
            self.root / "test-only-release-trust"
        )
        self.source, document = bundled_reviewed_source_and_document(
            (
                "Permanent opholdstilladelse can require Prøve i Dansk 2.\n\n"
                "An equivalent or higher Danish test can satisfy the official requirement."
            )
        )
        release_dir = self.root / "chunked-release"
        build_chunked_release_fixture(
            release_dir=release_dir,
            release_id="kr-2026-07-30.2",
            source=self.source,
            document=document,
            release_trust=self.release_trust,
            created_at_utc="2026-07-30T11:00:00Z",
        )
        install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )

    def test_chunked_source_installs_retrieves_cites_and_persists_full_provenance(self):
        result = AnswerService(
            retriever=HybridRetriever.from_data_dir(
                self.data_dir,
                embedding_provider=self.embedding_provider,
            ),
            generator=ChunkCitationGenerator(),
        ).answer(
            "Can an equivalent or higher Danish test satisfy the permanent residence requirement?",
            ProviderConfiguration(
                provider_id="openai_compatible",
                endpoint="http://127.0.0.1:1234",
                model="chunk-fixture-model",
                provider_version="fixture",
                model_identity={"id": "chunk-fixture-model"},
                capabilities=["generation"],
                validated_at_utc="2026-07-30T11:30:00Z",
            ),
        )
        record = ConversationStore(
            self.data_dir / "conversations.sqlite3"
        ).save_answer(
            question=result.question,
            normalized_question=result.normalized_question,
            answer=result.answer,
            model_identity=result.model_identity,
            corpus_identity=result.corpus_identity,
        )

        citation = record["answer"]["citations"][0]
        self.assertEqual(citation["citation_id"], citation["chunk_id"])
        self.assertEqual(citation["source_id"], self.source["source_id"])
        self.assertEqual(
            citation["source_document_id"],
            "di-rag-doc-permanent-residence-language",
        )
        self.assertEqual(citation["publisher"], self.source["publisher"])
        self.assertEqual(citation["official_url"], self.source["official_url"])
        self.assertEqual(citation["review_state"], self.source["review_state"])
        self.assertEqual(
            citation["checked_at_utc"],
            self.source["last_checked_at_utc"],
        )
        self.assertEqual(
            citation["source_content_sha256"],
            self.source["source_content_sha256"],
        )
        self.assertEqual(
            citation["normalized_document_sha256"],
            self.source["normalized_document_sha256"],
        )
        self.assertEqual(citation["corpus_identity"], "kr-2026-07-30.2")
        self.assertEqual(citation["knowledge_release_id"], "kr-2026-07-30.2")


if __name__ == "__main__":
    unittest.main()
