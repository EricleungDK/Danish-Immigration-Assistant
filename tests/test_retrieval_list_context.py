import unittest

from danish_rag.retrieval import _include_introductory_list_context


class IntroductoryListContextTests(unittest.TestCase):
    def documents(self, introduction="List of recognized qualifications:"):
        bodies = {
            "intro": ("list", 0, introduction),
            "tail": ("list", 1, "Certificate X and examination Y."),
            "reserved": ("requirements", 0, "A separate requested requirement."),
            "filler": ("other", 0, "Additional related information."),
        }
        return {key: {
            "document_id": key, "source_id": source, "chunk_index": index,
            "content": content, "title": "Approved source", "language": "en-GB",
            "review_state": "approved-current", "source_health": "healthy",
            "last_checked_at_utc": "2026-09-05T00:00:00Z",
            "fresh_tomato_inputs": {"next_review_due_utc": "2099-01-01T00:00:00Z"},
        } for key, (source, index, content) in bodies.items()}

    def select(self, documents, requested_exams=None):
        return _include_introductory_list_context(
            ["reserved", "tail", "filler"], documents_by_id=documents,
            metadata_filter={}, reserved_ids={"reserved"},
            requested_exams=requested_exams or set(), limit=3,
        )

    def test_explicit_introductory_colon_adds_separate_context_within_budget(self):
        self.assertEqual(self.select(self.documents()), ["reserved", "tail", "intro"])
        self.assertEqual(self.select(self.documents("Liste over godkendte kvalifikationer:")),
                         ["reserved", "tail", "intro"])

    def test_noninitial_passage_without_introductory_colon_is_not_automatically_paired(self):
        for text in ("An introduction.", "List of recognized qualifications.", "Details: general text."):
            with self.subTest(text=text):
                self.assertEqual(self.select(self.documents(text)),
                                 ["reserved", "tail", "filler"])

    def test_blocked_or_unapproved_context_cannot_be_added(self):
        for field, value in (("source_health", "broken"),
                             ("source_health", "changed-unreviewed"),
                             ("review_state", "unapproved")):
            documents = self.documents()
            documents["intro"][field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(self.select(documents), ["reserved", "tail", "filler"])

    def test_existing_named_exam_coverage_wins_over_context_candidate(self):
        documents = self.documents()
        documents["filler"]["content"] = "Prøve i Dansk 1 has a written component."
        self.assertEqual(self.select(documents, {"pd1"}), ["reserved", "tail", "filler"])
