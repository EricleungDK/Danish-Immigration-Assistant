import hashlib
import unittest

from danish_rag.semantic_chunks import SemanticChunkError, build_stable_semantic_chunks


def reviewed_source(*, source_id: str = "official-source") -> dict[str, object]:
    return {
        "source_id": source_id,
        "publisher": "SIRI",
        "title": "Reviewed official source",
        "official_url": "https://example.test/official-source",
        "final_url": "https://example.test/official-source",
        "language": "da",
        "review_state": "approved-current",
        "last_checked_at_utc": "2026-07-30T08:00:00Z",
        "normalized_document_sha256": (
            "c6d5dd1fb90594171db245b26e0f62ba0ac77ea6efa92512006218386d44f13a"
        ),
    }


def normalized_document(
    *,
    source_id: str = "official-source",
    content: str = "First reviewed fact.\n\nSecond reviewed fact.",
) -> dict[str, object]:
    return {
        "document_id": "reviewed-source-document",
        "source_id": source_id,
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
        "content": content,
    }


class StableSemanticChunkTests(unittest.TestCase):
    def test_content_must_match_the_reviewed_normalized_content_identity(self):
        with self.assertRaisesRegex(
            SemanticChunkError,
            "does not match the reviewed normalized content identity",
        ):
            build_stable_semantic_chunks(
                source=reviewed_source(),
                document=normalized_document(content="Changed unreviewed content."),
            )

    def test_source_document_identity_must_be_a_non_empty_string(self):
        with self.assertRaisesRegex(
            SemanticChunkError,
            "missing its identity",
        ):
            build_stable_semantic_chunks(
                source=reviewed_source(),
                document={**normalized_document(), "document_id": None},
            )

    def test_approved_source_identity_must_be_a_non_empty_string(self):
        with self.assertRaisesRegex(
            SemanticChunkError,
            "reviewed source identity",
        ):
            build_stable_semantic_chunks(
                source={**reviewed_source(), "source_id": None},
                document={**normalized_document(), "source_id": None},
            )

    def test_normalized_source_content_must_be_a_string(self):
        with self.assertRaisesRegex(
            SemanticChunkError,
            "string content",
        ):
            build_stable_semantic_chunks(
                source={
                    **reviewed_source(),
                    "normalized_document_sha256": hashlib.sha256(
                        "None".encode("utf-8")
                    ).hexdigest(),
                },
                document={**normalized_document(), "content": None},
            )

    def test_stable_source_identities_reject_controls_and_surrounding_whitespace(self):
        for source_id in (
            "official\0source",
            " official-source ",
            "official-\ud800source",
        ):
            with self.subTest(source_id=source_id):
                with self.assertRaisesRegex(
                    SemanticChunkError,
                    "valid reviewed source identity",
                ):
                    build_stable_semantic_chunks(
                        source={**reviewed_source(), "source_id": source_id},
                        document={**normalized_document(), "source_id": source_id},
                    )

    def test_stable_source_document_identity_rejects_controls_and_whitespace(self):
        for document_id in (
            "reviewed\0document",
            " reviewed-document ",
            "reviewed-\ud800document",
        ):
            with self.subTest(document_id=document_id):
                with self.assertRaisesRegex(
                    SemanticChunkError,
                    "missing its identity",
                ):
                    build_stable_semantic_chunks(
                        source=reviewed_source(),
                        document={**normalized_document(), "document_id": document_id},
                    )

    def test_reviewed_source_is_deterministically_divided_into_source_bound_chunks(self):
        source = reviewed_source()
        document = normalized_document()

        first = build_stable_semantic_chunks(source=source, document=document)
        repeated = build_stable_semantic_chunks(
            source={**source, "title": "Display title changed"},
            document={**document, "title": "Display title changed"},
        )
        different_source = build_stable_semantic_chunks(
            source=reviewed_source(source_id="other-official-source"),
            document=normalized_document(source_id="other-official-source"),
        )

        self.assertEqual(
            [chunk["content"] for chunk in first],
            ["First reviewed fact.", "Second reviewed fact."],
        )
        self.assertEqual(
            [chunk["chunk_id"] for chunk in repeated],
            [chunk["chunk_id"] for chunk in first],
        )
        self.assertNotEqual(first[0]["chunk_id"], first[1]["chunk_id"])
        self.assertNotEqual(
            [chunk["chunk_id"] for chunk in different_source],
            [chunk["chunk_id"] for chunk in first],
        )
        self.assertEqual(
            [chunk["document_id"] for chunk in first],
            [chunk["chunk_id"] for chunk in first],
        )
        self.assertEqual(
            [chunk["source_document_id"] for chunk in first],
            ["reviewed-source-document", "reviewed-source-document"],
        )
        self.assertEqual([chunk["chunk_index"] for chunk in first], [0, 1])
        self.assertTrue(
            all(
                chunk["normalized_document_sha256"]
                == "c6d5dd1fb90594171db245b26e0f62ba0ac77ea6efa92512006218386d44f13a"
                and chunk["source_id"] == "official-source"
                and len(chunk["chunk_content_sha256"]) == 64
                for chunk in first
            )
        )


if __name__ == "__main__":
    unittest.main()
