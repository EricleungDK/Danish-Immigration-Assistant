import json
import tempfile
import unittest
from pathlib import Path

from danish_rag.answer_pipeline import AnswerResult
from danish_rag.final_answer_evaluation import (
    AnswerServiceCaseRunner,
    CaseExecution,
)
from danish_rag.grounded_flexibility_evaluation import (
    generate_grounded_flexibility_evaluation,
    load_grounded_flexibility_dataset,
    write_grounded_flexibility_evaluation_report,
)
from danish_rag.knowledge_release import install_minimal_knowledge_release
from danish_rag.provider_setup import ProviderConfiguration
from danish_rag.retrieval import HybridRetriever
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = (
    ROOT / "data" / "evaluation" / "grounded-flexibility-v0.1-candidate.json"
)
MACHINE_REPORT_PATH = ROOT / "docs" / "progress" / "issue-48-grounded-flexibility.json"


class GroundedFlexibilityAnswerGenerator:
    """Controlled external-provider boundary scoped to the evaluated facts."""

    evaluated_document_ids = {
        "di-rag-doc-permanent-residence-language",
        "di-rag-doc-registration-deadlines-2026",
    }

    def generate(
        self,
        *,
        question,
        normalized_question,
        evidence,
        configuration,
        schema,
    ):
        del question, normalized_question, configuration, schema
        material_evidence = [
            item
            for item in evidence
            if item["document_id"] in self.evaluated_document_ids
        ]
        return {
            "summary": "The approved material sources support the facts below.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": item["content"],
                    "citation_ids": [item["citation_id"]],
                }
                for item in material_evidence
            ],
        }


def build_production_answer_runner(data_dir):
    embedding_provider = DeterministicEmbeddingProviderFixture()
    install_minimal_knowledge_release(
        data_dir,
        embedding_provider=embedding_provider,
    )
    return AnswerServiceCaseRunner(
        retriever=HybridRetriever.from_data_dir(
            data_dir,
            embedding_provider=embedding_provider,
        ),
        generator=GroundedFlexibilityAnswerGenerator(),
        configuration=ProviderConfiguration(
            provider_id="controlled",
            endpoint="in-process",
            model="controlled-evidence-copy-v1",
            provider_version="in-process-v1",
            model_identity={"id": "controlled-evidence-copy-v1"},
            capabilities=["generation"],
            validated_at_utc="2026-07-30T16:00:00Z",
        ),
    )


class SyntheticAnswerRunner:
    public_identity = {
        "provider_id": "controlled",
        "model": "issue-48-fixture",
        "corpus_id": "kr-2026-07-06.1",
    }

    def __init__(
        self,
        *,
        omit_document_id: str | None = None,
        omit_from_case_id: str | None = None,
    ) -> None:
        self.omit_document_id = omit_document_id
        self.omit_from_case_id = omit_from_case_id

    def run(self, case):
        required_document_ids = list(
            dict.fromkeys(
                document_id
                for intent in case["expected_intents"]
                for document_id in intent["required_document_ids"]
            )
        )
        if case["case_id"] == self.omit_from_case_id:
            required_document_ids = [
                document_id
                for document_id in required_document_ids
                if document_id != self.omit_document_id
            ]
        evidence = [
            self._evidence(document_id) for document_id in required_document_ids
        ]
        sections = [
            {
                "kind": "official_fact",
                "text": item["content"],
                "citation_ids": [item["citation_id"]],
            }
            for item in evidence
        ]
        if case["expected_behavior"] == "answer-with-refusal":
            sections.append(
                {
                    "kind": "refusal",
                    "text": "I am unable to determine your individual qualification.",
                    "citation_ids": [],
                }
            )
        result = AnswerResult(
            question=case["prompt"],
            normalized_question=case["prompt"].casefold(),
            answer={
                "summary": f"Synthetic answer for {case['case_id']}.",
                "response_kind": "answer",
                "sections": sections,
                "trust": {
                    "evidence_confidence": "High",
                    "fresh_tomato_score": "High",
                },
            },
            model_identity={"provider_id": "controlled", "model": "fixture"},
            corpus_identity="kr-2026-07-06.1",
        )
        return CaseExecution(
            case_id=case["case_id"],
            result=result,
            evidence=evidence,
        )

    @staticmethod
    def _evidence(document_id):
        content_by_id = {
            "di-rag-doc-permanent-residence-language": (
                "The applicant must pass Danish language test 2 (Pr\u00f8ve i Dansk 2), "
                "or a Danish exam of an equivalent or higher level."
            ),
            "di-rag-doc-registration-deadlines-2026": (
                "Users register for a test directly at the sprogcenter where they want "
                "to take the test. Current logistics must be verified on the official "
                "page."
            ),
        }
        domain = (
            "nyidanmark.dk"
            if document_id == "di-rag-doc-permanent-residence-language"
            else "danskogproever.dk"
        )
        return {
            "document_id": document_id,
            "citation_id": document_id,
            "content": content_by_id[document_id],
            "official_url": f"https://{domain}/{document_id}",
            "approval_state": "approved",
            "review_state": "approved-current",
            "source_health": "healthy",
            "agreement_state": "supports",
        }


class IncompleteFactRunner(SyntheticAnswerRunner):
    def run(self, case):
        execution = super().run(case)
        for section in execution.result.answer["sections"]:
            if section.get("citation_ids") == [
                "di-rag-doc-registration-deadlines-2026"
            ]:
                section["text"] = (
                    "Candidates register for a test directly at the sprogcenter where "
                    "they want to take the test."
                )
        return execution


class WrongScopeRefusalRunner(SyntheticAnswerRunner):
    def run(self, case):
        execution = super().run(case)
        for section in execution.result.answer["sections"]:
            if section.get("kind") == "refusal":
                section["text"] = "I cannot decide which exam date is most convenient."
        return execution


class FailedExecutionRunner(SyntheticAnswerRunner):
    def run(self, case):
        if case["case_id"] == "gf-pr-language-01":
            return CaseExecution(
                case_id=case["case_id"],
                result=None,
                evidence=[],
                error_type="FixtureExecutionError",
            )
        return super().run(case)


class OverdueUsableEvidenceRunner(SyntheticAnswerRunner):
    def run(self, case):
        execution = super().run(case)
        for evidence in execution.evidence:
            evidence["review_state"] = "overdue-policy-usable"
            evidence["source_health"] = "overdue-policy-usable"
        return execution


class GroundedFlexibilityDatasetContractTests(unittest.TestCase):
    def test_reviewed_cases_cover_low_overlap_paraphrases_and_composite_evidence(self):
        dataset = load_grounded_flexibility_dataset(DATASET_PATH)

        self.assertEqual(dataset["review_issue"], 48)
        self.assertFalse(dataset["privacy"]["uses_production_user_questions"])
        self.assertFalse(dataset["privacy"]["uses_production_user_conversations"])
        self.assertFalse(dataset["privacy"]["uses_production_user_answers"])

        cases_by_group = {}
        for case in dataset["cases"]:
            cases_by_group.setdefault(case["paraphrase_group_id"], []).append(case)

        for intent in dataset["selected_intents"]:
            with self.subTest(intent_id=intent["intent_id"]):
                variants = cases_by_group[intent["paraphrase_group_id"]]
                self.assertGreaterEqual(len(variants), 3)
                self.assertTrue(
                    all(
                        any(
                            expected["intent_id"] == intent["intent_id"]
                            for expected in case["expected_intents"]
                        )
                        for case in variants
                    )
                )

        multi_intent_cases = [
            case for case in dataset["cases"] if len(case["expected_intents"]) > 1
        ]
        self.assertGreaterEqual(len(multi_intent_cases), 2)
        for case in multi_intent_cases:
            with self.subTest(case_id=case["case_id"]):
                self.assertTrue(case["requires_cross_document_synthesis"])
                self.assertTrue(case["requires_claim_adjacent_citations"])
                for expected_intent in case["expected_intents"]:
                    self.assertTrue(expected_intent["required_document_ids"])
                    self.assertTrue(expected_intent["required_fact_ids"])

        self.assertTrue(
            any(
                case["expected_behavior"] == "answer-with-refusal"
                and case["unsupported_part_ids"]
                for case in dataset["cases"]
            )
        )

    def test_composite_case_fails_if_any_intent_loses_required_evidence(self):
        complete = generate_grounded_flexibility_evaluation(
            DATASET_PATH,
            runner=SyntheticAnswerRunner(),
            generated_at_utc="2026-07-30T16:00:00Z",
        )

        by_intent = complete["metrics"]["per_intent_evidence_coverage"]["by_intent"]
        self.assertEqual(
            by_intent["permanent-residence-language"]["observed"],
            1.0,
        )
        self.assertEqual(by_intent["pd3-registration-logistics"]["observed"], 1.0)

        incomplete = generate_grounded_flexibility_evaluation(
            DATASET_PATH,
            runner=SyntheticAnswerRunner(
                omit_document_id="di-rag-doc-registration-deadlines-2026",
                omit_from_case_id="gf-composite-01",
            ),
            generated_at_utc="2026-07-30T16:00:00Z",
        )

        composite = next(
            result
            for result in incomplete["case_results"]
            if result["case_id"] == "gf-composite-01"
        )
        registration = next(
            intent
            for intent in composite["intent_evidence"]
            if intent["intent_id"] == "pd3-registration-logistics"
        )
        self.assertEqual(
            registration["missing_document_ids"],
            ["di-rag-doc-registration-deadlines-2026"],
        )
        self.assertEqual(composite["status"], "failed")
        self.assertEqual(
            incomplete["metrics"]["per_intent_evidence_coverage"]["by_intent"][
                "pd3-registration-logistics"
            ]["status"],
            "failed",
        )
        self.assertFalse(incomplete["strict_passed"])

    def test_citing_a_document_does_not_credit_a_fact_missing_from_the_answer(self):
        report = generate_grounded_flexibility_evaluation(
            DATASET_PATH,
            runner=IncompleteFactRunner(),
            generated_at_utc="2026-07-30T16:00:00Z",
        )

        composite = next(
            result
            for result in report["case_results"]
            if result["case_id"] == "gf-composite-01"
        )
        self.assertEqual(
            composite["missing_fact_ids"],
            ["pd3-logistics-require-current-verification"],
        )
        self.assertEqual(composite["status"], "failed")
        self.assertEqual(
            report["metrics"]["required_fact_coverage"]["status"], "failed"
        )
        self.assertFalse(report["strict_passed"])

    def test_refusal_precision_requires_the_declared_unsupported_part(self):
        report = generate_grounded_flexibility_evaluation(
            DATASET_PATH,
            runner=WrongScopeRefusalRunner(),
            generated_at_utc="2026-07-30T16:00:00Z",
        )

        partial = next(
            result
            for result in report["case_results"]
            if result["case_id"] == "gf-supported-with-refusal-01"
        )
        self.assertEqual(partial["observed_unsupported_part_ids"], [])
        self.assertEqual(partial["unmatched_refusal_count"], 1)
        refusal = report["metrics"]["refusal_precision"]
        self.assertEqual(refusal["status"], "failed")
        self.assertEqual(refusal["missing_required_refusal_count"], 1)
        self.assertEqual(refusal["false_positive_refusal_count"], 1)

    def test_unevaluable_case_cannot_hide_inside_aggregate_metrics(self):
        report = generate_grounded_flexibility_evaluation(
            DATASET_PATH,
            runner=FailedExecutionRunner(),
            generated_at_utc="2026-07-30T16:00:00Z",
        )

        self.assertEqual(
            report["metrics"]["citation_correctness"]["status"],
            "not_evaluable",
        )
        self.assertEqual(
            report["metrics"]["unsupported_claim_rate"]["status"],
            "not_evaluable",
        )
        self.assertFalse(report["strict_passed"])

    def test_policy_usable_overdue_evidence_remains_eligible(self):
        report = generate_grounded_flexibility_evaluation(
            DATASET_PATH,
            runner=OverdueUsableEvidenceRunner(),
            generated_at_utc="2026-07-30T16:00:00Z",
        )

        self.assertEqual(
            report["metrics"]["per_intent_evidence_coverage"]["status"],
            "passed",
        )

    def test_report_publishes_safe_answer_metrics_and_fact_set_consistency(self):
        report = generate_grounded_flexibility_evaluation(
            DATASET_PATH,
            runner=SyntheticAnswerRunner(),
            generated_at_utc="2026-07-30T16:00:00Z",
        )

        self.assertEqual(
            set(report["metrics"]),
            {
                "per_intent_evidence_coverage",
                "required_fact_coverage",
                "citation_correctness",
                "unsupported_claim_rate",
                "refusal_precision",
                "paraphrase_consistency",
            },
        )
        for metric in report["metrics"].values():
            self.assertEqual(metric["status"], "passed")
        self.assertTrue(report["strict_passed"])
        self.assertEqual(report["metrics"]["required_fact_coverage"]["observed"], 1.0)
        self.assertEqual(report["metrics"]["citation_correctness"]["observed"], 1.0)
        self.assertEqual(report["metrics"]["unsupported_claim_rate"]["observed"], 0.0)
        self.assertEqual(report["metrics"]["refusal_precision"]["observed"], 1.0)

        consistency = report["metrics"]["paraphrase_consistency"]
        self.assertEqual(
            consistency["comparison_basis"],
            ["supported_fact_ids", "material_citation_ids"],
        )
        self.assertFalse(consistency["requires_identical_answer_prose"])
        self.assertGreaterEqual(consistency["group_count"], 3)

        composite = next(
            result
            for result in report["case_results"]
            if result["case_id"] == "gf-composite-01"
        )
        self.assertEqual(
            composite["supported_fact_ids"],
            [
                "pd3-logistics-require-current-verification",
                "pd3-registration-at-sprogcenter",
                "permanent-residence-pd2-basic-requirement",
            ],
        )
        self.assertEqual(
            composite["material_citation_ids"],
            [
                "di-rag-doc-permanent-residence-language",
                "di-rag-doc-registration-deadlines-2026",
            ],
        )

        serialized = str(report).casefold()
        self.assertNotIn("'prompt':", serialized)
        self.assertNotIn("'question':", serialized)
        self.assertNotIn("'answer':", serialized)
        self.assertNotIn("conversation_id", serialized)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.json"
            write_grounded_flexibility_evaluation_report(report, output_path)
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), report)

    def test_published_machine_report_is_content_free_and_traceable(self):
        published = json.loads(MACHINE_REPORT_PATH.read_text(encoding="utf-8"))

        self.assertTrue(published["strict_passed"])
        self.assertEqual(
            published["dataset"]["dataset_id"],
            "di-rag-grounded-flexibility-v0.1-candidate",
        )
        self.assertRegex(published["dataset"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(
            all(metric["status"] == "passed" for metric in published["metrics"].values())
        )
        serialized = json.dumps(published).casefold()
        self.assertNotIn('"prompt"', serialized)
        self.assertNotIn('"question"', serialized)
        self.assertNotIn('"answer":', serialized)
        self.assertNotIn("conversation_id", serialized)

    def test_production_answer_path_qualifies_every_synthetic_variant(self):
        from tests.source_freshness_fixture import fixture_review_time

        with fixture_review_time(), tempfile.TemporaryDirectory() as tmpdir:
            report = generate_grounded_flexibility_evaluation(
                DATASET_PATH,
                runner=build_production_answer_runner(tmpdir),
                generated_at_utc="2026-07-30T16:00:00Z",
            )

        self.assertTrue(report["strict_passed"], report)
        self.assertTrue(
            all(result["status"] == "passed" for result in report["case_results"]),
            report["case_results"],
        )


if __name__ == "__main__":
    unittest.main()
