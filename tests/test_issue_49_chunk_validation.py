import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from danish_rag.knowledge_release import KnowledgeReleaseError, verify_knowledge_release
from danish_rag.release_trust import sign_manifest
from danish_rag.semantic_chunks import stable_chunk_id
from danish_rag.source_maintenance import build_publishable_knowledge_release
from tests.chunk_release_fixture import normalized_content_sha256
from tests.release_trust_fixture import create_test_release_trust_fixture


class ChunkReleaseValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.release_dir = self.root / "chunked-release"
        self.release_trust = create_test_release_trust_fixture(
            self.root / "test-only-release-trust"
        )
        normalized_content = "First reviewed fact.\n\nSecond reviewed fact."
        source = {
            "source_id": "official-source",
            "publisher": "SIRI",
            "title": "Reviewed official source",
            "official_url": "https://example.test/official-source",
            "final_url": "https://example.test/official-source",
            "topic": "language requirement",
            "language": "da",
            "review_state": "approved-current",
            "reviewed_at_utc": "2026-07-30T08:30:00Z",
            "reviewers": ["fixture-reviewer"],
            "last_checked_at_utc": "2026-07-30T08:00:00Z",
            "source_content_sha256": "b" * 64,
            "normalized_document_sha256": normalized_content_sha256(
                normalized_content
            ),
            "extraction_schema_version": "1.0",
            "fresh_tomato_inputs": {
                "next_review_due_utc": "2026-10-30T08:00:00Z",
                "source_health": "current",
            },
        }
        self.source = source
        document = {
            "document_id": "reviewed-source-document",
            "source_id": "official-source",
            "title": "Reviewed official source",
            "publisher": "SIRI",
            "official_url": "https://example.test/official-source",
            "final_url": "https://example.test/official-source",
            "language": "da",
            "topic_tags": ["language-requirement"],
            "review_state": "approved-current",
            "approval_state": "approved",
            "source_health": "healthy",
            "checked_at_utc": "2026-07-30T08:00:00Z",
            "content": normalized_content,
        }
        build_publishable_knowledge_release(
            release_dir=self.release_dir,
            release_id="kr-2026-07-30.3",
            source_registry_version="sr-2026-07-30.1",
            sources=[source],
            documents=[document],
            created_at_utc="2026-07-30T12:00:00Z",
            minimum_application_version="0.1.0",
            corpus_schema_version="2.0",
            signing_private_key_path=self.release_trust.signing_private_key_path,
            trust_root_path=self.release_trust.trust_root_path,
        )

    def rewrite_first_chunk_and_resign(self, **updates: object) -> None:
        documents_path = self.release_dir / "corpus" / "documents.json"
        documents = json.loads(documents_path.read_text(encoding="utf-8"))
        documents[0].update(updates)
        self.write_documents_and_resign(documents)

    def write_documents_and_resign(
        self,
        documents: list[dict[str, Any]],
        *,
        manifest: dict[str, Any] | None = None,
    ) -> None:
        documents_path = self.release_dir / "corpus" / "documents.json"
        documents_bytes = (
            json.dumps(documents, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        documents_path.write_bytes(documents_bytes)

        manifest_path = self.release_dir / "manifest.json"
        if manifest is None:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"][0]["sha256"] = hashlib.sha256(
            documents_bytes
        ).hexdigest()
        manifest["artifacts"][0]["bytes"] = len(documents_bytes)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sign_manifest(
            manifest_path,
            self.release_trust.signing_private_key_path,
            self.release_dir / "manifest.sig",
        )

    def test_chunked_release_without_explicit_content_unit_schema_is_rejected(self):
        manifest_path = self.release_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.pop("content_unit_schema_version")
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sign_manifest(
            manifest_path,
            self.release_trust.signing_private_key_path,
            self.release_dir / "manifest.sig",
        )

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "semantic chunk content-unit schema",
        ):
            verify_knowledge_release(
                self.release_dir,
                trust_root_path=self.release_trust.trust_root_path,
            )

    def test_chunked_release_requires_valid_source_content_identity(self):
        manifest_path = self.release_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sources"][0]["source_content_sha256"] = ""
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sign_manifest(
            manifest_path,
            self.release_trust.signing_private_key_path,
            self.release_dir / "manifest.sig",
        )

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "valid source content SHA-256 identity",
        ):
            verify_knowledge_release(
                self.release_dir,
                trust_root_path=self.release_trust.trust_root_path,
            )

    def test_chunked_release_rejects_duplicate_source_identities(self):
        manifest_path = self.release_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sources"].append(dict(manifest["sources"][0]))
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sign_manifest(
            manifest_path,
            self.release_trust.signing_private_key_path,
            self.release_dir / "manifest.sig",
        )

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "duplicate approved source identity",
        ):
            verify_knowledge_release(
                self.release_dir,
                trust_root_path=self.release_trust.trust_root_path,
            )

    def test_chunked_release_requires_structured_human_review_evidence(self):
        invalid_values = {
            "reviewers": "not-a-list",
            "reviewed_at_utc": "not-a-time",
        }
        for field, invalid_value in invalid_values.items():
            with self.subTest(field=field):
                manifest_path = self.release_dir / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                original_value = manifest["sources"][0][field]
                manifest["sources"][0][field] = invalid_value
                manifest_path.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                sign_manifest(
                    manifest_path,
                    self.release_trust.signing_private_key_path,
                    self.release_dir / "manifest.sig",
                )

                with self.assertRaisesRegex(
                    KnowledgeReleaseError,
                    "valid human reviewer evidence",
                ):
                    verify_knowledge_release(
                        self.release_dir,
                        trust_root_path=self.release_trust.trust_root_path,
                    )

                manifest["sources"][0][field] = original_value
                manifest_path.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                sign_manifest(
                    manifest_path,
                    self.release_trust.signing_private_key_path,
                    self.release_dir / "manifest.sig",
                )

    def test_chunked_release_requires_non_empty_release_and_corpus_identities(self):
        for field in ("knowledge_release_id", "corpus_id"):
            with self.subTest(field=field):
                manifest_path = self.release_dir / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                original_value = manifest[field]
                manifest[field] = ""
                manifest_path.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                sign_manifest(
                    manifest_path,
                    self.release_trust.signing_private_key_path,
                    self.release_dir / "manifest.sig",
                )

                with self.assertRaisesRegex(
                    KnowledgeReleaseError,
                    f"valid {field.replace('_', ' ')}",
                ):
                    verify_knowledge_release(
                        self.release_dir,
                        trust_root_path=self.release_trust.trust_root_path,
                    )

                manifest[field] = original_value
                manifest_path.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                sign_manifest(
                    manifest_path,
                    self.release_trust.signing_private_key_path,
                    self.release_dir / "manifest.sig",
                )

    def test_chunked_release_rejects_control_characters_in_release_identity(self):
        manifest_path = self.release_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["knowledge_release_id"] = "bad\0id"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sign_manifest(
            manifest_path,
            self.release_trust.signing_private_key_path,
            self.release_dir / "manifest.sig",
        )

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "valid knowledge release id",
        ):
            verify_knowledge_release(
                self.release_dir,
                trust_root_path=self.release_trust.trust_root_path,
            )

    def test_whole_document_schema_rejects_chunk_shaped_documents(self):
        chunk_documents = json.loads(
            (self.release_dir / "corpus" / "documents.json").read_text(
                encoding="utf-8"
            )
        )

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "whole-document corpus cannot contain semantic chunk provenance",
        ):
            build_publishable_knowledge_release(
                release_dir=self.root / "mislabelled-whole-document-release",
                release_id="kr-2026-07-30-mislabelled",
                source_registry_version="sr-2026-07-30.1",
                sources=[dict(self.source)],
                documents=chunk_documents,
                created_at_utc="2026-07-30T12:30:00Z",
                minimum_application_version="0.1.0",
                corpus_schema_version="1.0",
                signing_private_key_path=self.release_trust.signing_private_key_path,
                trust_root_path=self.release_trust.trust_root_path,
            )

    def test_chunked_release_with_content_not_matching_chunk_identity_is_rejected(self):
        self.rewrite_first_chunk_and_resign(
            content="Content changed after stable chunk identity was assigned."
        )

        with self.assertRaisesRegex(KnowledgeReleaseError, "chunk content hash"):
            verify_knowledge_release(
                self.release_dir,
                trust_root_path=self.release_trust.trust_root_path,
            )

    def test_chunked_release_with_forged_source_content_identity_is_rejected(self):
        self.rewrite_first_chunk_and_resign(
            chunk_id="official-source::chunk::forged",
            document_id="official-source::chunk::forged",
        )

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "source and chunk content identity",
        ):
            verify_knowledge_release(
                self.release_dir,
                trust_root_path=self.release_trust.trust_root_path,
            )

    def test_chunked_release_rejects_null_source_document_identity(self):
        documents_path = self.release_dir / "corpus" / "documents.json"
        documents = json.loads(documents_path.read_text(encoding="utf-8"))
        for document in documents:
            chunk_id = stable_chunk_id(
                source_id="official-source",
                source_document_id="None",
                chunk_content_sha256=document["chunk_content_sha256"],
                occurrence=0,
            )
            document.update(
                source_document_id=None,
                chunk_id=chunk_id,
                document_id=chunk_id,
            )
        self.write_documents_and_resign(documents)

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "source document identity",
        ):
            verify_knowledge_release(
                self.release_dir,
                trust_root_path=self.release_trust.trust_root_path,
            )

    def test_chunked_release_rejects_blank_source_identity(self):
        documents_path = self.release_dir / "corpus" / "documents.json"
        documents = json.loads(documents_path.read_text(encoding="utf-8"))
        for document in documents:
            chunk_id = stable_chunk_id(
                source_id="",
                source_document_id=document["source_document_id"],
                chunk_content_sha256=document["chunk_content_sha256"],
                occurrence=0,
            )
            document.update(
                source_id="",
                chunk_id=chunk_id,
                document_id=chunk_id,
            )
        manifest_path = self.release_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sources"][0]["source_id"] = ""
        self.write_documents_and_resign(documents, manifest=manifest)

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "valid approved source identity",
        ):
            verify_knowledge_release(
                self.release_dir,
                trust_root_path=self.release_trust.trust_root_path,
            )

    def test_chunked_release_rejects_blank_source_check_time(self):
        documents_path = self.release_dir / "corpus" / "documents.json"
        documents = json.loads(documents_path.read_text(encoding="utf-8"))
        for document in documents:
            document["checked_at_utc"] = ""
        manifest_path = self.release_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sources"][0]["last_checked_at_utc"] = ""
        self.write_documents_and_resign(documents, manifest=manifest)

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "valid source check time",
        ):
            verify_knowledge_release(
                self.release_dir,
                trust_root_path=self.release_trust.trust_root_path,
            )

    def test_chunked_release_rejects_non_string_chunk_content(self):
        documents_path = self.release_dir / "corpus" / "documents.json"
        document = json.loads(documents_path.read_text(encoding="utf-8"))[0]
        content_hash = hashlib.sha256("None".encode("utf-8")).hexdigest()
        chunk_id = stable_chunk_id(
            source_id="official-source",
            source_document_id=document["source_document_id"],
            chunk_content_sha256=content_hash,
            occurrence=0,
        )
        document.update(
            content=None,
            chunk_content_sha256=content_hash,
            normalized_document_sha256=content_hash,
            chunk_index=0,
            chunk_id=chunk_id,
            document_id=chunk_id,
        )
        manifest_path = self.release_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sources"][0]["normalized_document_sha256"] = content_hash
        self.write_documents_and_resign([document], manifest=manifest)

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "non-empty string content",
        ):
            verify_knowledge_release(
                self.release_dir,
                trust_root_path=self.release_trust.trust_root_path,
            )

    def test_chunk_sequence_must_match_the_reviewed_normalized_source_hash(self):
        changed_content = "Changed content with an internally consistent chunk identity."
        changed_content_hash = hashlib.sha256(
            changed_content.encode("utf-8")
        ).hexdigest()
        changed_chunk_id = stable_chunk_id(
            source_id="official-source",
            source_document_id="reviewed-source-document",
            chunk_content_sha256=changed_content_hash,
            occurrence=0,
        )
        self.rewrite_first_chunk_and_resign(
            content=changed_content,
            chunk_content_sha256=changed_content_hash,
            chunk_id=changed_chunk_id,
            document_id=changed_chunk_id,
        )

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "reviewed normalized content identity",
        ):
            verify_knowledge_release(
                self.release_dir,
                trust_root_path=self.release_trust.trust_root_path,
            )


if __name__ == "__main__":
    unittest.main()
