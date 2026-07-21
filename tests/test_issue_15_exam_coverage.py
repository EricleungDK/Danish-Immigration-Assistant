import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any

from danish_rag.answer_pipeline import (
    AnswerService,
    AnswerValidationError,
    answer_schema,
)
from danish_rag.knowledge_release import (
    BUNDLED_MINIMAL_RELEASE,
    install_minimal_knowledge_release,
)
from danish_rag.provider_setup import ProviderConfiguration
from danish_rag.retrieval import HybridRetriever
from danish_rag.evaluation_quality_bar import load_evaluation_cases
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture


ROOT = Path(__file__).resolve().parents[1]


def provider_configuration() -> ProviderConfiguration:
    return ProviderConfiguration(
        provider_id="openai_compatible",
        endpoint="http://127.0.0.1:1234",
        model="fixture-model",
        provider_version="fixture-provider",
        model_identity={"id": "fixture-model"},
        capabilities=["generation"],
        validated_at_utc="2026-07-06T12:00:00+00:00",
    )


class RecordingGenerator:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "question": question,
                "normalized_question": normalized_question,
                "evidence_ids": [item["citation_id"] for item in evidence],
                "schema": schema,
            }
        )
        return self.payload


class Issue15ExamCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.data_dir = Path(self.tempdir.name)
        self.embedding_provider = DeterministicEmbeddingProviderFixture()
        self.installation = install_minimal_knowledge_release(
            self.data_dir,
            embedding_provider=self.embedding_provider,
        )
        self.retriever = HybridRetriever.from_data_dir(
            self.data_dir,
            embedding_provider=self.embedding_provider,
        )

    def test_added_sources_include_review_and_provenance_metadata(self):
        manifest = self.installation["manifest"]
        source_by_id = {source["source_id"]: source for source in manifest["sources"]}

        for source_id in {
            "nyidanmark-permanent-residence-language-requirements",
            "nyidanmark-equivalent-tests-language-test-2",
            "nyidanmark-equivalent-tests-language-test-3",
            "danskogproever-danish-exam-overview",
            "danskogproever-registration-deadlines-2026",
        }:
            source = source_by_id[source_id]
            for field in {
                "publisher",
                "topic",
                "language",
                "fresh_tomato_inputs",
                "review_state",
                "reviewers",
                "source_content_sha256",
                "normalized_document_sha256",
                "official_url",
                "final_url",
                "last_checked_at_utc",
                "reviewed_at_utc",
                "extraction_schema_version",
            }:
                self.assertIn(field, source, source_id)
            self.assertEqual(source["review_state"], "approved-current")
            self.assertTrue(source["reviewers"])

        documents = self.installation["documents"]
        self.assertGreaterEqual(len(documents), 5)
        for document in documents:
            self.assertEqual(document["approval_state"], "approved")
            self.assertEqual(document["content_origin"], "project-authored-fixture")
            self.assertIn(document["source_id"], source_by_id)

    def test_english_and_danish_queries_retrieve_exam_comparison_sources(self):
        cases = {
            "Can you compare Prøve i Dansk 1, Prøve i Dansk 2, Prøve i Dansk 3, and Studieprøven?": {
                "di-rag-doc-danish-exam-overview",
                "di-rag-doc-permanent-residence-language",
                "di-rag-doc-equivalent-tests-language-test-3",
            },
            "sammenlign Prøve i Dansk 1 Prøve i Dansk 2 Prøve i Dansk 3 Studieprøven permanent ophold": {
                "di-rag-doc-danish-exam-overview",
                "di-rag-doc-permanent-residence-language",
                "di-rag-doc-equivalent-tests-language-test-3",
            },
        }

        for query, expected_ids in cases.items():
            with self.subTest(query=query):
                result_ids = {
                    item["document_id"]
                    for item in self.retriever.retrieve(query, limit=5)
                }
                self.assertTrue(expected_ids.issubset(result_ids), result_ids)

    def test_registration_logistics_retrieves_official_dates_and_signup_boundary(self):
        results = self.retriever.retrieve(
            "Where do I register for Prøve i Dansk 3 and what should I check before signing up?",
            limit=3,
        )

        self.assertEqual(results[0]["document_id"], "di-rag-doc-registration-deadlines-2026")
        self.assertIn("sprogcenter", results[0]["content"])
        self.assertIn("31. august 2026", results[0]["content"])
        self.assertIn("Prøve i Dansk 3", results[0]["content"])

    def test_permanent_residence_exam_comparison_keeps_personal_choice_boundary(self):
        generator = RecordingGenerator(
            {
                "summary": "The official sources document examination facts.",
                "sections": [
                    {
                        "kind": "official_fact",
                        "text": (
                            "For permanent opholdstilladelse, New to Denmark states a "
                            "basic Danish-language requirement: the applicant must pass "
                            "Danish language test 2 (Prøve i Dansk 2), or a Danish exam "
                            "of an equivalent or higher level."
                        ),
                        "citation_ids": [
                            "di-rag-doc-permanent-residence-language"
                        ],
                    }
                ],
            }
        )

        result = AnswerService(retriever=self.retriever, generator=generator).answer(
            (
                "Can you compare Prøve i Dansk 1, Prøve i Dansk 2, Prøve i Dansk 3, "
                "and Studieprøven for permanent residence?"
            ),
            provider_configuration(),
        )

        refusal_sections = [
            section
            for section in result.answer["sections"]
            if section["kind"] == "refusal"
        ]
        self.assertEqual(result.answer["response_kind"], "answer")
        self.assertEqual(len(refusal_sections), 1)
        self.assertIn("cannot recommend which examination", refusal_sections[0]["text"])

    def test_certificate_acceptance_question_answers_facts_but_refuses_decision(self):
        generator = RecordingGenerator(
            {
                "summary": "Official sources document equivalence lists, not a personal certificate decision.",
                "sections": [
                    {
                        "kind": "official_fact",
                        "text": (
                            "The official list for Danish language test 3 includes "
                            "Studieprøven and previous tests that can still qualify."
                        ),
                        "citation_ids": ["di-rag-doc-equivalent-tests-language-test-3"],
                    }
                ],
            }
        )

        result = AnswerService(retriever=self.retriever, generator=generator).answer(
            "I have an old Danish certificate. Can you tell me whether SIRI will accept it for permanent residence?",
            provider_configuration(),
        )

        self.assertEqual(
            generator.calls[0]["schema"],
            answer_schema(
                [
                    "di-rag-doc-permanent-residence-language",
                    "di-rag-doc-equivalent-tests-language-test-3",
                    "di-rag-doc-equivalent-tests-language-test-2",
                ]
            ),
        )
        self.assertIn(
            "di-rag-doc-equivalent-tests-language-test-3",
            generator.calls[0]["evidence_ids"],
        )
        refusal_sections = [
            section
            for section in result.answer["sections"]
            if section["kind"] == "refusal"
        ]
        self.assertEqual(len(refusal_sections), 1)
        self.assertIn("cannot decide whether SIRI will accept", refusal_sections[0]["text"])
        self.assertEqual(result.answer["response_kind"], "answer")

    def test_certificate_acceptance_conclusion_from_generator_is_rejected(self):
        generator = RecordingGenerator(
            {
                "summary": "SIRI will accept your old certificate.",
                "sections": [
                    {
                        "kind": "official_fact",
                        "text": "Your certificate will be accepted for permanent residence.",
                        "citation_ids": ["di-rag-doc-equivalent-tests-language-test-3"],
                    }
                ],
            }
        )

        with self.assertRaisesRegex(AnswerValidationError, "prohibited"):
            AnswerService(retriever=self.retriever, generator=generator).answer(
                "I have an old Danish certificate. Can you tell me whether SIRI will accept it for permanent residence?",
                provider_configuration(),
            )

    def test_reviewed_evaluation_cases_cover_issue_15_content_areas(self):
        cases = load_evaluation_cases(
            ROOT / "data" / "evaluation" / "evaluation-set-v0.1-candidate.json"
        )
        by_area = {case["content_area"]: case for case in cases["cases"]}

        for area in {
            "danish-examination-types",
            "registration-logistics",
            "certificate-equivalence-boundaries",
        }:
            self.assertIn(area, by_area)
            case = by_area[area]
            self.assertTrue(case["retrieval_expectations"]["required_facts"])
            self.assertTrue(case["final_answer_expectations"]["required_facts"])

    def test_manifest_hash_matches_expanded_corpus_artifact(self):
        manifest = self.installation["manifest"]
        artifact = next(
            artifact
            for artifact in manifest["artifacts"]
            if artifact["path"] == "corpus/documents.json"
        )
        corpus_path = BUNDLED_MINIMAL_RELEASE / "corpus" / "documents.json"

        self.assertEqual(corpus_path.stat().st_size, artifact["bytes"])
        digest = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
        self.assertEqual(digest, artifact["sha256"])


if __name__ == "__main__":
    unittest.main()
