import unittest

from danish_rag.retrieval import (
    _fts_match_expression, _mentioned_exams, _prefer_named_exam_passages,
    normalize_question,
)


class RetrievalQueryTermsTests(unittest.TestCase):
    def test_exam_levels_survive_function_word_removal(self):
        terms = _fts_match_expression(normalize_question(
            "Please tell me what PD2 establishes for residence and where I can enroll for PD3."
        )).split(" OR ")
        self.assertIn("2", terms)
        self.assertIn("3", terms)
        self.assertNotIn("me", terms)
        self.assertNotIn("for", terms)

    def test_generic_exam_phrasing_keeps_danish_search_terms(self):
        for question in (
            "Which examination level is required?",
            "What does the exam establish?",
            "Which language test applies?",
        ):
            with self.subTest(question=question):
                self.assertIn("Prøve i Dansk", normalize_question(question))
        self.assertNotIn("Prøve i Dansk", normalize_question("Give an example of housing."))

    def test_explicit_exam_names_are_distinct_from_numbers_and_dates(self):
        for query in ("PD2", "Prøve i Dansk 2", "Danish language test 2", "Danish test 2"):
            with self.subTest(query=query):
                self.assertEqual(_mentioned_exams(query), {"pd2"})
        self.assertEqual(_mentioned_exams("PD1, PD3 and Studieprøven"),
                         {"pd1", "pd3", "studieprøven"})
        self.assertEqual(_mentioned_exams("3 years, level 2, 2026-01-01"), set())

    def test_body_preference_keeps_source_positions_and_multiple_exams(self):
        documents = {
            "intro": {"source_id": "a", "content": "Eligibility overview",
                      "title": "PD2 and PD3 requirements"},
            "other": {"source_id": "b", "content": "Registration details"},
            "two": {"source_id": "a", "content": "Prøve i Dansk 2 requirement"},
            "both": {"source_id": "a", "content": "Danish test 2 and Prøve i Dansk 3"},
        }
        ranking = ["intro", "other", "two", "both"]
        self.assertEqual(_prefer_named_exam_passages(
            "Compare PD2 and PD3", ranking, documents_by_id=documents,
        ), ["two", "other", "both", "intro"])
        self.assertEqual(_prefer_named_exam_passages(
            "Residence rules for 2 years", ranking, documents_by_id=documents,
        ), ranking)
        self.assertEqual(_prefer_named_exam_passages(
            "Danish examination requirements", ranking, documents_by_id=documents,
        ), ["both", "other", "two", "intro"])
