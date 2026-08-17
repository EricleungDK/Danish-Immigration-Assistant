import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from danish_rag.knowledge_release import (
    KnowledgeReleaseError,
    active_corpus_summary,
    install_knowledge_release,
    install_minimal_knowledge_release,
)
from danish_rag.retrieval import HybridRetriever, build_hybrid_index
from tests.chunk_release_fixture import (
    build_chunked_release_fixture,
    bundled_reviewed_source_and_document,
)
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture
from tests.release_trust_fixture import create_test_release_trust_fixture


class ChunkInstallationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.data_dir = self.root / "data"
        self.embedding_provider = DeterministicEmbeddingProviderFixture()
        self.release_trust = create_test_release_trust_fixture(
            self.root / "test-only-release-trust"
        )
        self.source, self.document = bundled_reviewed_source_and_document(
            (
                "Permanent opholdstilladelse can require Prøve i Dansk 2.\n\n"
                "An equivalent or higher Danish test can also satisfy the official requirement."
            )
        )

    def build_chunked_release(self, release_id: str = "kr-2026-07-30.1") -> Path:
        release_dir = self.root / release_id
        return build_chunked_release_fixture(
            release_dir=release_dir,
            release_id=release_id,
            source=self.source,
            document=self.document,
            release_trust=self.release_trust,
            created_at_utc="2026-07-30T10:00:00Z",
        )

    def test_chunked_index_identity_and_retrieval_provenance_are_distinct_from_whole_documents(
        self,
    ):
        whole_document_install = install_minimal_knowledge_release(
            self.data_dir,
            embedding_provider=self.embedding_provider,
        )
        self.assertEqual(
            whole_document_install["index"]["schema_version"],
            "hybrid-index-v1",
        )

        chunked_install = install_knowledge_release(
            self.data_dir,
            release_dir=self.build_chunked_release(),
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertEqual(
            chunked_install["index"]["schema_version"],
            "hybrid-chunk-index-v1",
        )
        self.assertEqual(
            chunked_install["index"]["content_unit_schema_version"],
            "semantic-chunk-v1",
        )
        result = next(
            item
            for item in HybridRetriever.from_data_dir(
                self.data_dir,
                embedding_provider=self.embedding_provider,
            ).retrieve("Which equivalent or higher Danish test is described?", limit=2)
            if "equivalent or higher" in item["content"]
        )
        self.assertEqual(result["citation_id"], result["chunk_id"])
        self.assertEqual(result["source_id"], self.source["source_id"])
        self.assertEqual(result["publisher"], self.source["publisher"])
        self.assertEqual(result["official_url"], self.source["official_url"])
        self.assertEqual(result["review_state"], self.source["review_state"])
        self.assertEqual(
            result["checked_at_utc"],
            self.source["last_checked_at_utc"],
        )
        self.assertEqual(result["corpus_identity"], "kr-2026-07-30.1")
        self.assertEqual(result["knowledge_release_id"], "kr-2026-07-30.1")

    def test_legacy_manifest_without_explicit_corpus_schema_keeps_whole_document_index(
        self,
    ):
        manifest = {
            "corpus_id": "legacy-whole-document-corpus",
            "knowledge_release_id": "legacy-whole-document-release",
        }

        metadata = build_hybrid_index(
            self.data_dir,
            [self.document],
            manifest=manifest,
            embedding_provider=self.embedding_provider,
        )

        self.assertEqual(metadata["schema_version"], "hybrid-index-v1")
        self.assertNotIn("content_unit_schema_version", metadata)

    def test_failed_chunked_index_build_preserves_prior_whole_document_pair(self):
        install_minimal_knowledge_release(
            self.data_dir,
            embedding_provider=self.embedding_provider,
        )

        def fail_chunk_embedding(phase: str) -> None:
            if phase == "embedding":
                raise RuntimeError("simulated chunk embedding failure")

        with self.assertRaisesRegex(RuntimeError, "chunk embedding failure"):
            install_knowledge_release(
                self.data_dir,
                release_dir=self.build_chunked_release(),
                embedding_provider=self.embedding_provider,
                trust_root_path=self.release_trust.trust_root_path,
                fault_injector=fail_chunk_embedding,
            )

        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            "kr-2026-07-06.1",
        )
        results = HybridRetriever.from_data_dir(
            self.data_dir,
            embedding_provider=self.embedding_provider,
        ).retrieve("What Danish test supports permanent residence?")
        self.assertTrue(results)
        self.assertEqual(results[0]["knowledge_release_id"], "kr-2026-07-06.1")
        self.assertNotIn("chunk_id", results[0])

    def test_already_active_chunked_release_rebuilds_an_incompatible_index(self):
        release_dir = self.build_chunked_release()
        install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        index_dir = self.data_dir / "index" / "kr-2026-07-30.1"
        for filename in ("index-metadata.json", "dense-index.json"):
            path = index_dir / filename
            value = json.loads(path.read_text(encoding="utf-8"))
            metadata = value["metadata"] if filename == "dense-index.json" else value
            metadata["schema_version"] = "hybrid-index-v1"
            metadata.pop("corpus_schema_version", None)
            metadata.pop("content_unit_schema_version", None)
            metadata.pop("indexed_unit", None)
            path.write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        repeated = install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertNotEqual(repeated["progress"][-1]["phase"], "already_active")
        self.assertEqual(repeated["index"]["schema_version"], "hybrid-chunk-index-v1")

    def test_already_active_chunked_release_rebuilds_malformed_dense_metadata(self):
        release_dir = self.build_chunked_release()
        install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        dense_index_path = (
            self.data_dir
            / "index"
            / "kr-2026-07-30.1"
            / "dense-index.json"
        )
        dense_index = json.loads(dense_index_path.read_text(encoding="utf-8"))
        dense_index["metadata"] = []
        dense_index_path.write_text(
            json.dumps(dense_index, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        repeated = install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertNotEqual(repeated["progress"][-1]["phase"], "already_active")
        self.assertEqual(repeated["index"]["schema_version"], "hybrid-chunk-index-v1")

    def test_already_active_chunked_release_rebuilds_malformed_dense_vectors(self):
        release_dir = self.build_chunked_release()
        install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        dense_index_path = (
            self.data_dir
            / "index"
            / "kr-2026-07-30.1"
            / "dense-index.json"
        )
        dense_index = json.loads(dense_index_path.read_text(encoding="utf-8"))
        dense_index["vectors"] = None
        dense_index_path.write_text(
            json.dumps(dense_index, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        repeated = install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertNotEqual(repeated["progress"][-1]["phase"], "already_active")
        self.assertEqual(repeated["index"]["schema_version"], "hybrid-chunk-index-v1")

    def test_already_active_chunked_release_rebuilds_valid_tampered_dense_vectors(self):
        release_dir = self.build_chunked_release()
        installation = install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        dense_index_path = (
            self.data_dir
            / "index"
            / "kr-2026-07-30.1"
            / "dense-index.json"
        )
        dense_index = json.loads(dense_index_path.read_text(encoding="utf-8"))
        vector_dimensions = installation["index"]["vector_dimensions"]
        dense_index["vectors"][0]["vector"] = [0.123456] * vector_dimensions
        dense_index_path.write_text(
            json.dumps(dense_index, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        repeated = install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertNotEqual(repeated["progress"][-1]["phase"], "already_active")

    def test_normal_retrieval_rejects_tampered_active_corpus(self):
        release_dir = self.build_chunked_release()
        install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        documents_path = (
            self.data_dir
            / "corpus"
            / "kr-2026-07-30.1"
            / "documents.json"
        )
        documents = json.loads(documents_path.read_text(encoding="utf-8"))
        documents[0]["content"] = "UNSIGNED POISON"
        documents_path.write_text(
            json.dumps(documents, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "artifact integrity",
        ):
            HybridRetriever.from_data_dir(
                self.data_dir,
                embedding_provider=self.embedding_provider,
            )

    def test_already_active_chunked_release_rebuilds_tampered_corpus(self):
        release_dir = self.build_chunked_release()
        install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        documents_path = (
            self.data_dir
            / "corpus"
            / "kr-2026-07-30.1"
            / "documents.json"
        )
        documents = json.loads(documents_path.read_text(encoding="utf-8"))
        documents[0]["content"] = "UNSIGNED POISON"
        documents_path.write_text(
            json.dumps(documents, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        repeated = install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertNotEqual(repeated["progress"][-1]["phase"], "already_active")
        active_documents = json.loads(documents_path.read_text(encoding="utf-8"))
        self.assertNotEqual(active_documents[0]["content"], "UNSIGNED POISON")

    def test_same_release_id_with_new_signed_content_replaces_active_corpus(self):
        release_dir = self.build_chunked_release()
        install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        changed_content = "A newly reviewed signed fact replaces the prior content."
        changed_source, changed_document = bundled_reviewed_source_and_document(
            changed_content
        )
        replacement_dir = self.root / "replacement-same-id"
        build_chunked_release_fixture(
            release_dir=replacement_dir,
            release_id="kr-2026-07-30.1",
            source=changed_source,
            document=changed_document,
            release_trust=self.release_trust,
            created_at_utc="2026-07-30T11:00:00Z",
        )

        repeated = install_knowledge_release(
            self.data_dir,
            release_dir=replacement_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertNotEqual(repeated["progress"][-1]["phase"], "already_active")
        self.assertEqual(repeated["documents"][0]["content"], changed_content)

    def test_already_active_chunked_release_rebuilds_tampered_lexical_index(self):
        release_dir = self.build_chunked_release()
        install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        lexical_path = (
            self.data_dir
            / "index"
            / "kr-2026-07-30.1"
            / "lexical.sqlite3"
        )
        connection = sqlite3.connect(lexical_path)
        try:
            connection.execute("UPDATE documents_fts SET content = 'UNSIGNED POISON'")
            connection.commit()
        finally:
            connection.close()

        repeated = install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertNotEqual(repeated["progress"][-1]["phase"], "already_active")


if __name__ == "__main__":
    unittest.main()
