import unittest

from danish_rag.retrieval import _title_ranked_source_representatives


class SourceTitleRankingTests(unittest.TestCase):
    def test_repeated_chunks_cast_one_title_vote_using_best_retrieved_chunk(self):
        documents = {
            "a2": {"source_id": "a", "title": "Residence requirements"},
            "a1": {"source_id": "a", "title": "Residence requirements"},
            "b": {"source_id": "b", "title": "Test equivalence"},
        }
        self.assertEqual(
            _title_ranked_source_representatives(
                "residence", ["b", "a2", "a1"], documents_by_id=documents,
            ),
            ["a2"],
        )

    def test_stopwords_and_unmatched_titles_do_not_add_votes(self):
        documents = {"a": {"source_id": "a", "title": "Tests equivalent to Danish"}}
        for query in ("Where to enroll?", "Enrollment", "til og med"):
            with self.subTest(query=query):
                self.assertEqual(_title_ranked_source_representatives(
                    query, ["a"], documents_by_id=documents,
                ), [])

    def test_title_ties_keep_original_relevance_order(self):
        documents = {
            "b": {"source_id": "b", "title": "Danish test B"},
            "a": {"source_id": "a", "title": "Danish test A"},
        }
        self.assertEqual(_title_ranked_source_representatives(
            "Danish test", ["b", "a"], documents_by_id=documents,
        ), ["b", "a"])
