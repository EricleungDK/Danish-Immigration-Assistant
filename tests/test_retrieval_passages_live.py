"""Opt-in passage-availability checks against the installed candidate and model."""

import json
import os
import unittest
from pathlib import Path

from danish_rag.knowledge_release import default_data_dir
from danish_rag.retrieval import HybridRetriever

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("DI_RAG_RUN_LIVE_PASSAGE_RETRIEVAL") == "1",
    "set DI_RAG_RUN_LIVE_PASSAGE_RETRIEVAL=1 to check installed-candidate passages",
)
class LivePassageRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.retriever = HybridRetriever.from_data_dir(
            default_data_dir(),
            trust_root_path=ROOT / "config/trust_roots/project-release-key-v2.json",
        )

    def assert_requirement_passage(self, results):
        self.assertTrue(any(
            "Du skal have bestået Prøve i Dansk 2 eller en danskprøve "
            "på et tilsvarende eller højere niveau." in result["content"]
            or "grundlæggende betingelse om at have bestået Prøve i Dansk 2 "
            "eller en danskprøve på et tilsvarende eller højere niveau."
            in result["content"]
            for result in results
        ), "Missing basic language-requirement passage")

    def test_all_grounded_flexibility_intents_have_actual_passages(self):
        dataset = json.loads((
            ROOT / "data/evaluation/grounded-flexibility-v0.1-candidate.json"
        ).read_text())
        for case in dataset["cases"]:
            with self.subTest(case_id=case["case_id"]):
                results = self.retriever.retrieve(case["prompt"], limit=3)
                for intent in case["expected_intents"]:
                    if intent["intent_id"] == "permanent-residence-language":
                        self.assert_requirement_passage(results)
                    if intent["intent_id"] == "pd3-registration-logistics":
                        self.assertTrue(any(
                            "Du tilmelder dig en prøve direkte ved det sprogcenter, "
                            "hvor du ønsker at tage prøven." in result["content"]
                            for result in results
                        ), "Missing signup-location passage")

    def test_final_answer_requirement_and_comparison_passages(self):
        dataset = json.loads((
            ROOT / "data/evaluation/evaluation-set-v0.1-candidate.json"
        ).read_text())
        cases = {case["id"]: case for case in dataset["cases"]}
        for case_id in (
            "eval-001-permanent-language-supported", "eval-014-citation-validation",
        ):
            with self.subTest(case_id=case_id):
                self.assert_requirement_passage(
                    self.retriever.retrieve(cases[case_id]["prompt"], limit=3)
                )
        results = self.retriever.retrieve(
            cases["eval-002-exam-type-comparison"]["prompt"], limit=3,
        )
        for passage in (
            "Prøve i Dansk 1 (PD1) består af en skriftlig og en mundtlig del.",
            "Studieprøven består af en skriftlig og en mundtlig del.",
        ):
            self.assertTrue(any(passage in result["content"] for result in results))

    def test_certificate_list_has_separate_introductory_context_candidate(self):
        dataset = json.loads((
            ROOT / "data/evaluation/evaluation-set-v0.1-candidate.json"
        ).read_text())
        case = next(case for case in dataset["cases"]
                    if case["id"] == "eval-004-certificate-equivalence-boundary")
        results = self.retriever.retrieve(case["prompt"], limit=3)
        self.assertEqual(len(results), 3)
        introductions = [result for result in results if
                         "Below is a list of Danish language tests that are equivalent "
                         "to or higher than the Danish language test 3." in result["content"]]
        tails = [result for result in results
                 if "Previous tests which still qualify" in result["content"]]
        self.assertTrue(introductions)
        self.assertTrue(tails)
        self.assertEqual(introductions[0]["source_id"], tails[0]["source_id"])
        self.assertNotEqual(introductions[0]["document_id"], tails[0]["document_id"])
