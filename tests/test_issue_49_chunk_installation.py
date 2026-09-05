import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import danish_rag.knowledge_release as knowledge_release_module
from danish_rag.knowledge_release import (
    KnowledgeReleaseError,
    active_corpus_summary,
    install_knowledge_release,
    install_minimal_knowledge_release,
    load_active_release,
)
from danish_rag.retrieval import (
    HybridRetriever,
    RetrievalError,
    build_hybrid_index,
)
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
                trust_root_path=self.release_trust.trust_root_path,
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
                trust_root_path=self.release_trust.trust_root_path,
            )

    def test_signed_manifest_rejects_tampered_corpus_and_active_hash_evidence(self):
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
        encoded_documents = (
            json.dumps(documents, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        documents_path.write_bytes(encoded_documents)
        active_path = self.data_dir / "active-release.json"
        active = json.loads(active_path.read_text(encoding="utf-8"))
        active["manifest"]["artifacts"][0]["sha256"] = hashlib.sha256(
            encoded_documents
        ).hexdigest()
        active["manifest"]["artifacts"][0]["bytes"] = len(encoded_documents)
        active_path.write_text(
            json.dumps(active, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(KnowledgeReleaseError, "signed manifest"):
            HybridRetriever.from_data_dir(
                self.data_dir,
                embedding_provider=self.embedding_provider,
                trust_root_path=self.release_trust.trust_root_path,
            )

    def test_active_record_cannot_redirect_the_installed_trust_anchor(self):
        release_dir = self.build_chunked_release()
        install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        active_path = self.data_dir / "active-release.json"
        active = json.loads(active_path.read_text(encoding="utf-8"))
        active["trust_root_path"] = str(self.root / "attacker-controlled-root.json")
        active_path.write_text(
            json.dumps(active, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        retriever = HybridRetriever.from_data_dir(
            self.data_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertEqual(
            retriever.manifest["knowledge_release_id"],
            "kr-2026-07-30.1",
        )

    def test_custom_trust_anchor_must_be_supplied_by_trusted_configuration(self):
        release_dir = self.build_chunked_release()
        install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )

        with self.assertRaisesRegex(KnowledgeReleaseError, "explicit trust anchor"):
            HybridRetriever.from_data_dir(
                self.data_dir,
                embedding_provider=self.embedding_provider,
            )

        retriever = HybridRetriever.from_data_dir(
            self.data_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        self.assertEqual(
            retriever.manifest["knowledge_release_id"],
            "kr-2026-07-30.1",
        )

    def test_chunk_active_record_requires_derived_index_integrity_evidence(self):
        release_dir = self.build_chunked_release()
        install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        active_path = self.data_dir / "active-release.json"
        active = json.loads(active_path.read_text(encoding="utf-8"))
        active.pop("index_artifacts")
        active_path.write_text(
            json.dumps(active, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(KnowledgeReleaseError, "index artifact integrity"):
            HybridRetriever.from_data_dir(
                self.data_dir,
                embedding_provider=self.embedding_provider,
                trust_root_path=self.release_trust.trust_root_path,
            )

    def test_same_id_pointer_write_failure_restores_previous_active_pair(self):
        release_dir = self.build_chunked_release()
        original = install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        changed_content = "Replacement content must roll back on pointer failure."
        changed_source, changed_document = bundled_reviewed_source_and_document(
            changed_content
        )
        replacement_dir = self.root / "rollback-same-id"
        build_chunked_release_fixture(
            release_dir=replacement_dir,
            release_id="kr-2026-07-30.1",
            source=changed_source,
            document=changed_document,
            release_trust=self.release_trust,
            created_at_utc="2026-07-30T11:00:00Z",
        )

        original_write_json_atomic = knowledge_release_module._write_json_atomic

        def conditional_write(value: dict, path: Path) -> None:
            if path.name == "active-release.json":
                raise OSError("simulated active pointer failure")
            original_write_json_atomic(value, path)

        with patch(
            "danish_rag.knowledge_release._write_json_atomic",
            side_effect=conditional_write,
        ):
            with self.assertRaisesRegex(OSError, "active pointer failure"):
                install_knowledge_release(
                    self.data_dir,
                    release_dir=replacement_dir,
                    embedding_provider=self.embedding_provider,
                    trust_root_path=self.release_trust.trust_root_path,
                )

        active_documents = json.loads(
            Path(original["active"]["documents_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(active_documents, original["documents"])
        retriever = HybridRetriever.from_data_dir(
            self.data_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        self.assertNotIn(
            changed_content,
            " ".join(
                document["content"]
                for document in retriever.documents_by_id.values()
            ),
        )

    def test_same_id_index_backup_failure_preserves_previous_active_pair(self):
        release_dir = self.build_chunked_release()
        original = install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        changed_content = "Replacement content must not survive a failed backup move."
        changed_source, changed_document = bundled_reviewed_source_and_document(
            changed_content
        )
        replacement_dir = self.root / "backup-move-failure"
        build_chunked_release_fixture(
            release_dir=replacement_dir,
            release_id="kr-2026-07-30.1",
            source=changed_source,
            document=changed_document,
            release_trust=self.release_trust,
            created_at_utc="2026-07-30T11:00:00Z",
        )
        original_index_path = Path(original["active"]["index_path"])
        backup_index_path = original_index_path.with_name(
            f".{original_index_path.name}.backup"
        )
        original_replace = Path.replace

        def fail_index_backup(path: Path, target: Path) -> Path:
            if path == original_index_path and Path(target) == backup_index_path:
                raise OSError("simulated index backup move failure")
            return original_replace(path, target)

        with patch.object(Path, "replace", fail_index_backup):
            with self.assertRaisesRegex(OSError, "index backup move failure"):
                install_knowledge_release(
                    self.data_dir,
                    release_dir=replacement_dir,
                    embedding_provider=self.embedding_provider,
                    trust_root_path=self.release_trust.trust_root_path,
                )

        self.assertTrue(original_index_path.is_dir())
        retriever = HybridRetriever.from_data_dir(
            self.data_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        self.assertNotIn(
            changed_content,
            " ".join(
                document["content"]
                for document in retriever.documents_by_id.values()
            ),
        )

    def test_retrieval_uses_one_active_snapshot_if_pointer_changes_mid_load(self):
        first_install = install_knowledge_release(
            self.data_dir,
            release_dir=self.build_chunked_release("kr-2026-07-30.1"),
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        first_active = first_install["active"]
        second_content = (
            "A different release must not be mixed into the first snapshot."
        )
        second_source, second_document = bundled_reviewed_source_and_document(
            second_content
        )
        second_dir = self.root / "second-release"
        build_chunked_release_fixture(
            release_dir=second_dir,
            release_id="kr-2026-07-30.2",
            source=second_source,
            document=second_document,
            release_trust=self.release_trust,
            created_at_utc="2026-07-30T11:00:00Z",
        )
        second_install = install_knowledge_release(
            self.data_dir,
            release_dir=second_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        active_path = self.data_dir / "active-release.json"
        active_path.write_text(
            json.dumps(first_active, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        def switch_after_first_read(
            data_dir: str | Path,
            **_kwargs,
        ):
            snapshot = load_active_release(
                data_dir,
                trust_root_path=self.release_trust.trust_root_path,
            )
            active_path.write_text(
                json.dumps(second_install["active"], indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return snapshot

        with patch(
            "danish_rag.retrieval.load_active_release",
            side_effect=switch_after_first_read,
        ):
            retriever = HybridRetriever.from_data_dir(
                self.data_dir,
                embedding_provider=self.embedding_provider,
                trust_root_path=self.release_trust.trust_root_path,
            )

        self.assertEqual(
            retriever.manifest["knowledge_release_id"],
            "kr-2026-07-30.1",
        )
        self.assertNotIn(
            second_content,
            " ".join(
                document["content"]
                for document in retriever.documents_by_id.values()
            ),
        )

    def test_existing_retriever_keeps_immutable_snapshot_after_same_id_activation(self):
        release_dir = self.build_chunked_release()
        install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        original_retriever = HybridRetriever.from_data_dir(
            self.data_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        changed_content = "Replacement rows must not enter an existing retriever."
        changed_source, changed_document = bundled_reviewed_source_and_document(
            changed_content
        )
        replacement_dir = self.root / "immutable-snapshot-replacement"
        build_chunked_release_fixture(
            release_dir=replacement_dir,
            release_id="kr-2026-07-30.1",
            source=changed_source,
            document=changed_document,
            release_trust=self.release_trust,
            created_at_utc="2026-07-30T11:00:00Z",
        )
        install_knowledge_release(
            self.data_dir,
            release_dir=replacement_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )

        results = original_retriever.retrieve(
            "Which equivalent or higher Danish test is described?",
            limit=2,
        )

        self.assertTrue(results)
        self.assertNotIn(changed_content, " ".join(item["content"] for item in results))

    def test_legacy_index_without_digests_rejects_valid_vector_tampering(self):
        installation = install_minimal_knowledge_release(
            self.data_dir,
            embedding_provider=self.embedding_provider,
        )
        active_path = self.data_dir / "active-release.json"
        active = json.loads(active_path.read_text(encoding="utf-8"))
        active.pop("index_artifacts")
        active_path.write_text(
            json.dumps(active, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        dense_path = Path(installation["active"]["index_path"]) / "dense-index.json"
        dense = json.loads(dense_path.read_text(encoding="utf-8"))
        dense["vectors"][0]["vector"] = [
            0.123456
        ] * installation["index"]["vector_dimensions"]
        dense_path.write_text(
            json.dumps(dense, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RetrievalError, "content"):
            HybridRetriever.from_data_dir(
                self.data_dir,
                embedding_provider=self.embedding_provider,
            )

    def test_legacy_active_manifest_is_anchored_to_bundled_signed_release(self):
        install_minimal_knowledge_release(
            self.data_dir,
            embedding_provider=self.embedding_provider,
        )
        active_path = self.data_dir / "active-release.json"
        active = json.loads(active_path.read_text(encoding="utf-8"))
        for field in (
            "manifest_path",
            "signature_path",
            "trust_root_path",
            "index_artifacts",
        ):
            active.pop(field, None)
        active["manifest"].pop("corpus_schema_version", None)
        documents_path = Path(active["documents_path"])
        documents = json.loads(documents_path.read_text(encoding="utf-8"))
        documents[0]["content"] = "LEGACY POISON"
        encoded_documents = (
            json.dumps(documents, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        documents_path.write_bytes(encoded_documents)
        active["manifest"]["artifacts"][0]["sha256"] = hashlib.sha256(
            encoded_documents
        ).hexdigest()
        active["manifest"]["artifacts"][0]["bytes"] = len(encoded_documents)
        active_path.write_text(
            json.dumps(active, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        build_hybrid_index(
            self.data_dir,
            documents,
            manifest=active["manifest"],
            embedding_provider=self.embedding_provider,
        )

        with self.assertRaisesRegex(KnowledgeReleaseError, "trusted signed release"):
            HybridRetriever.from_data_dir(
                self.data_dir,
                embedding_provider=self.embedding_provider,
            )

    def test_interrupted_same_id_activation_recovers_stranded_backup(self):
        original = install_knowledge_release(
            self.data_dir,
            release_dir=self.build_chunked_release(),
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        replacement_content = "Interrupted replacement content."
        replacement_source, replacement_document = bundled_reviewed_source_and_document(
            replacement_content
        )
        replacement_release = self.root / "crash-replacement-release"
        build_chunked_release_fixture(
            release_dir=replacement_release,
            release_id="kr-2026-07-30.1",
            source=replacement_source,
            document=replacement_document,
            release_trust=self.release_trust,
            created_at_utc="2026-07-30T11:00:00Z",
        )
        replacement_data = self.root / "replacement-data"
        replacement = install_knowledge_release(
            replacement_data,
            release_dir=replacement_release,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        release_id = "kr-2026-07-30.1"
        final_corpus = self.data_dir / "corpus" / release_id
        final_index = self.data_dir / "index" / release_id
        backup_corpus = self.data_dir / "corpus" / f".{release_id}.backup"
        backup_index = self.data_dir / "index" / f".{release_id}.backup"
        final_corpus.replace(backup_corpus)
        final_index.replace(backup_index)
        shutil.copytree(Path(replacement["active"]["documents_path"]).parent, final_corpus)
        shutil.copytree(Path(replacement["active"]["index_path"]), final_index)
        active_path = self.data_dir / "active-release.json"
        shutil.copy2(active_path, self.data_dir / ".active-release.json.backup")
        (self.data_dir / ".active-release-transaction.json").write_text(
            json.dumps(
                {
                    "knowledge_release_id": release_id,
                    "active_release": replacement["active"],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        recovered = load_active_release(
            self.data_dir,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertEqual(recovered["manifest"], original["manifest"])
        self.assertFalse(backup_corpus.exists())
        self.assertFalse(backup_index.exists())
        recovered_documents = json.loads(
            Path(recovered["documents_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(recovered_documents, original["documents"])

    def test_interrupted_identical_release_reinstall_restores_previous_pair(self):
        release_dir = self.build_chunked_release()
        original = install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        replacement_data = self.root / "identical-replacement-data"
        replacement = install_knowledge_release(
            replacement_data,
            release_dir=release_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        release_id = "kr-2026-07-30.1"
        final_corpus = self.data_dir / "corpus" / release_id
        final_index = self.data_dir / "index" / release_id
        backup_corpus = self.data_dir / "corpus" / f".{release_id}.backup"
        backup_index = self.data_dir / "index" / f".{release_id}.backup"
        pending_index = self.data_dir / "index" / f".{release_id}.pending"
        final_corpus.replace(backup_corpus)
        final_index.replace(backup_index)
        shutil.copytree(Path(replacement["active"]["documents_path"]).parent, final_corpus)
        shutil.copytree(Path(replacement["active"]["index_path"]), pending_index)
        active_path = self.data_dir / "active-release.json"
        shutil.copy2(active_path, self.data_dir / ".active-release.json.backup")
        target_active = dict(replacement["active"])
        target_active.update(
            {
                "documents_path": str(final_corpus / "documents.json"),
                "index_path": str(final_index),
                "manifest_path": str(final_corpus / "manifest.json"),
                "signature_path": str(final_corpus / "manifest.sig"),
            }
        )
        (self.data_dir / ".active-release-transaction.json").write_text(
            json.dumps(
                {
                    "knowledge_release_id": release_id,
                    "active_release": target_active,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        recovered = load_active_release(
            self.data_dir,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertEqual(recovered, original["active"])
        self.assertTrue(final_index.is_dir())
        self.assertFalse(backup_corpus.exists())
        self.assertFalse(backup_index.exists())
        self.assertFalse(pending_index.exists())

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
