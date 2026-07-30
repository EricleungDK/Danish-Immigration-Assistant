import tempfile
import unittest
from pathlib import Path

from danish_rag.source_maintenance import build_publishable_knowledge_release
from tests.chunk_release_fixture import bundled_reviewed_source_and_document
from tests.release_trust_fixture import create_test_release_trust_fixture


class ChunkedKnowledgeReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.release_trust = create_test_release_trust_fixture(
            self.root / "test-only-release-trust"
        )
        self.source, self.document = bundled_reviewed_source_and_document(
            (
                "Permanent opholdstilladelse can require Prøve i Dansk 2.\n\n"
                "The reviewed source also describes an equivalent or higher Danish test."
            )
        )

    def test_chunked_release_authoring_declares_and_writes_semantic_chunk_schema(self):
        result = build_publishable_knowledge_release(
            release_dir=self.root / "chunked-release",
            release_id="kr-2026-07-30.1",
            source_registry_version="sr-2026-07-30.1",
            sources=[self.source],
            documents=[self.document],
            created_at_utc="2026-07-30T10:00:00Z",
            minimum_application_version="0.1.0",
            corpus_schema_version="2.0",
            signing_private_key_path=self.release_trust.signing_private_key_path,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertEqual(result["manifest"]["corpus_schema_version"], "2.0")
        self.assertEqual(
            result["manifest"]["content_unit_schema_version"],
            "semantic-chunk-v1",
        )
        self.assertEqual(len(result["documents"]), 2)
        self.assertEqual(
            [item["source_document_id"] for item in result["documents"]],
            [self.document["document_id"], self.document["document_id"]],
        )
        self.assertTrue(
            all(item["document_id"] == item["chunk_id"] for item in result["documents"])
        )


if __name__ == "__main__":
    unittest.main()
