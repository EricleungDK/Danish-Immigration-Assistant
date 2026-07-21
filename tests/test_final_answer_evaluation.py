import json
import hashlib
import stat
import tempfile
import unittest
from pathlib import Path

from danish_rag.answer_pipeline import AnswerResult
from danish_rag.final_answer_evaluation import (
    CaseExecution,
    FinalAnswerEvaluationError,
    build_live_ollama_runner,
    evaluate_final_answer_case,
    fingerprint_answer_review_payload,
    fingerprint_case_execution,
    generate_final_answer_evaluation,
    main,
)
from danish_rag.knowledge_release import install_minimal_knowledge_release
from danish_rag.provider_setup import ProviderConfiguration
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture


ROOT = Path(__file__).resolve().parents[1]


class FinalAnswerEvaluationPublicSeamTests(unittest.TestCase):
    def test_live_runner_uses_installed_production_retriever_and_records_public_identity(self):
        embedding_provider = DeterministicEmbeddingProviderFixture()
        with tempfile.TemporaryDirectory() as tmpdir:
            install_minimal_knowledge_release(
                tmpdir,
                embedding_provider=embedding_provider,
            )
            runner = build_live_ollama_runner(
                data_dir=tmpdir,
                configuration=ProviderConfiguration(
                    provider_id="ollama",
                    endpoint="http://127.0.0.1:11434",
                    model="gemma4:12b",
                    provider_version="0.30.6",
                    model_identity={"model": "gemma4:12b", "digest": "fixture-digest"},
                    capabilities=["completion"],
                    validated_at_utc="2026-07-13T12:00:00Z",
                ),
                embedding_provider=embedding_provider,
            )

        self.assertEqual(runner.public_identity["provider_id"], "ollama")
        self.assertEqual(runner.public_identity["model"], "gemma4:12b")
        self.assertEqual(
            runner.public_identity["model_identity"]["digest"], "fixture-digest"
        )
        self.assertEqual(runner.public_identity["corpus_id"], "kr-2026-07-06.1")

    def test_case_evaluation_scores_structural_claim_citation_and_trust_evidence(self):
        evidence = {
            "citation_id": "official-1",
            "content": "Prøve i Dansk 2 is listed as an official requirement.",
            "official_url": "https://www.nyidanmark.dk/official-1",
            "review_state": "approved-current",
            "source_health": "healthy",
            "approval_state": "approved",
            "agreement_state": "supports",
        }
        case = {
            "id": "eval-test-supported",
            "final_answer_expectations": {
                "expected_behavior": "answer",
                "required_facts": ["State the supported official requirement."],
                "forbidden_claims": ["The user qualifies."],
                "required_citation_domains": ["nyidanmark.dk"],
                "forbidden_source_domains": ["community.example"],
                "trust_indicators": [
                    "Evidence Confidence: High",
                    "Fresh Tomato Score: High",
                ],
            },
        }
        execution = CaseExecution(
            case_id=case["id"],
            result=AnswerResult(
                question="synthetic prompt",
                normalized_question="synthetic prompt",
                answer={
                    "summary": "See the cited official facts below.",
                    "response_kind": "answer",
                    "sections": [
                        {
                            "kind": "official_fact",
                            "text": evidence["content"],
                            "citation_ids": [evidence["citation_id"]],
                        }
                    ],
                    "citations": [],
                    "trust": {
                        "evidence_confidence": "High",
                        "fresh_tomato_score": "High",
                    },
                },
                model_identity={"provider_id": "controlled", "model": "fixture"},
                corpus_identity="fixture-corpus",
            ),
            evidence=[evidence],
        )

        result = evaluate_final_answer_case(case, execution)

        self.assertEqual(result["checks"]["behavior"]["status"], "passed")
        self.assertEqual(
            result["checks"]["official_fact_citation_coverage"]["status"],
            "passed",
        )
        self.assertEqual(
            result["checks"]["citation_correctness"]["status"], "passed"
        )
        self.assertEqual(
            result["checks"]["unsupported_claims"]["unsupported_count"], 0
        )
        self.assertEqual(
            result["checks"]["personal_eligibility_conclusions"]["count"], 0
        )
        self.assertEqual(
            result["checks"]["evidence_confidence"]["status"], "passed"
        )
        self.assertEqual(
            result["checks"]["fresh_tomato_min_material_source_rule"]["status"],
            "passed",
        )
        self.assertEqual(
            result["checks"]["required_source_domains"]["status"], "passed"
        )
        self.assertEqual(
            result["checks"]["forbidden_source_domains"]["status"], "passed"
        )
        self.assertEqual(result["checks"]["required_facts"]["status"], "not_evaluable")
        self.assertEqual(
            result["checks"]["forbidden_claims"]["status"], "not_evaluable"
        )

    def test_independent_adjudication_can_score_prose_expectations_and_paraphrase_support(self):
        evidence = {
            "citation_id": "official-1",
            "content": "Applicants must pass Danish language test 2.",
            "official_url": "https://nyidanmark.dk/official-1",
            "review_state": "approved-current",
            "source_health": "healthy",
            "approval_state": "approved",
            "agreement_state": "supports",
        }
        case = {
            "id": "eval-test-adjudicated",
            "evaluation_surface": "answer-path",
            "prompt": "synthetic prompt",
            "final_answer_expectations": {
                "expected_behavior": "answer",
                "required_facts": ["State the Danish language test 2 requirement."],
                "forbidden_claims": ["The user personally qualifies."],
                "required_citation_domains": ["nyidanmark.dk"],
                "forbidden_source_domains": [],
                "trust_indicators": ["Evidence Confidence", "Fresh Tomato Score"],
                "privacy_requirements": ["No answer-time network egress."],
            },
        }
        execution = CaseExecution(
            case_id=case["id"],
            result=AnswerResult(
                question="synthetic prompt",
                normalized_question="synthetic prompt",
                answer={
                    "summary": "See the cited official facts below.",
                    "response_kind": "answer",
                    "sections": [
                        {
                            "kind": "official_fact",
                            "text": "Danish language test 2 must be passed by applicants.",
                            "citation_ids": ["official-1"],
                        }
                    ],
                    "trust": {
                        "evidence_confidence": "High",
                        "fresh_tomato_score": "High",
                    },
                },
                model_identity={"provider_id": "controlled", "model": "fixture"},
                corpus_identity="fixture-corpus",
            ),
            evidence=[evidence],
        )

        execution_sha256 = fingerprint_case_execution(execution)
        adjudication = {
            "schema_version": "final-answer-case-adjudication-v1",
            "case_id": case["id"],
            "evaluation_surface": "answer-path",
            "evidence_binding": {
                "kind": "answer-review-payload",
                "sha256": fingerprint_answer_review_payload(case, execution),
                "execution_sha256": execution_sha256,
            },
            "assessment_method": "independent-human-review",
            "assertion_results": {
                "eval-test-adjudicated:required-facts:01": "passed",
                "eval-test-adjudicated:forbidden-claims:01": "passed",
                "eval-test-adjudicated:privacy-requirements:01": "passed",
            },
            "claim_support": {"section-1": {"official-1": True}},
        }

        result = evaluate_final_answer_case(
            case,
            execution,
            adjudication=adjudication,
        )

        self.assertEqual(result["checks"]["required_facts"]["status"], "passed")
        self.assertEqual(result["checks"]["required_facts"]["covered_count"], 1)
        self.assertEqual(result["checks"]["forbidden_claims"]["status"], "passed")
        self.assertEqual(result["checks"]["forbidden_claims"]["violation_count"], 0)
        self.assertEqual(result["checks"]["privacy_requirements"]["status"], "passed")
        self.assertEqual(result["execution_sha256"], execution_sha256)
        self.assertEqual(
            result["checks"]["citation_correctness"]["status"], "passed"
        )
        self.assertEqual(
            result["checks"]["unsupported_claims"]["unsupported_count"], 0
        )

        mismatched = json.loads(json.dumps(adjudication))
        mismatched["evidence_binding"]["execution_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            FinalAnswerEvaluationError,
            "does not match the exact answer execution",
        ):
            evaluate_final_answer_case(case, execution, adjudication=mismatched)

        changed_prompt_case = json.loads(json.dumps(case))
        changed_prompt_case["prompt"] += " changed"
        with self.assertRaisesRegex(
            FinalAnswerEvaluationError,
            "does not match the exact answer review payload",
        ):
            evaluate_final_answer_case(
                changed_prompt_case,
                execution,
                adjudication=adjudication,
            )

        changed_assertion_case = json.loads(json.dumps(case))
        changed_assertion_case["final_answer_expectations"]["required_facts"][
            0
        ] += " changed"
        with self.assertRaisesRegex(
            FinalAnswerEvaluationError,
            "does not match the exact answer review payload",
        ):
            evaluate_final_answer_case(
                changed_assertion_case,
                execution,
                adjudication=adjudication,
            )

    def test_controlled_cli_evaluates_approved_cases_without_recording_conversation_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "final-answer-evaluation.json"

            status = main(
                [
                    "--repo-root",
                    str(ROOT),
                    "--output",
                    str(output_path),
                    "--generated-at-utc",
                    "2026-07-13T12:00:00Z",
                ]
            )

            self.assertEqual(status, 0)
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["schema_version"], "final-answer-evaluation-v1")
            self.assertEqual(report["generated_at_utc"], "2026-07-13T12:00:00Z")
            self.assertEqual(report["dataset"]["case_count"], 20)
            self.assertTrue(report["retrieval_and_final_answer_evaluation_separate"])
            self.assertEqual(len(report["case_results"]), 20)
            self.assertEqual(report["execution"]["mode"], "controlled")
            self.assertEqual(report["identity"]["provider_id"], "controlled")
            self.assertEqual(report["identity"]["corpus_id"], "kr-2026-07-06.1")
            self.assertEqual(report["assertion_contract"]["assertion_count"], 158)
            self.assertFalse(report["adjudications"]["provided"])

            required_facts = report["metrics"]["required_fact_coverage"]
            self.assertEqual(required_facts["status"], "not_evaluable")
            self.assertIn("machine-readable", required_facts["reason"])
            self.assertEqual(
                set(report["metrics"]),
                {
                    "required_fact_coverage",
                    "forbidden_claims",
                    "privacy_requirement_compliance",
                    "official_fact_citation_coverage",
                    "citation_correctness",
                    "unsupported_claim_rate",
                    "personal_eligibility_conclusions",
                    "clarify_answer_refuse_accuracy",
                    "trust_indicator_correctness",
                    "fresh_tomato_min_material_source_rule_pass_rate",
                    "required_source_domain_coverage",
                    "forbidden_source_domain_violations",
                    "case_execution_errors",
                    "evaluation_surface_completion",
                },
            )
            self.assertEqual(
                report["metrics"]["official_fact_citation_coverage"]["threshold"],
                1.0,
            )
            self.assertEqual(
                report["metrics"]["official_fact_citation_coverage"]["status"],
                "not_evaluable",
            )
            self.assertEqual(
                report["metrics"]["personal_eligibility_conclusions"]["observed"],
                0,
            )
            self.assertEqual(
                report["metrics"]["personal_eligibility_conclusions"]["status"],
                "not_evaluable",
            )
            self.assertEqual(
                report["metrics"]["forbidden_source_domain_violations"]["status"],
                "not_evaluable",
            )
            self.assertEqual(
                report["metrics"]["privacy_requirement_compliance"]["status"],
                "not_evaluable",
            )
            self.assertFalse(report["strict_passed"])
            self.assertIn("required_fact_coverage", report["threshold_failures"])
            self.assertIn("evaluation_surface_completion", report["threshold_failures"])
            self.assertEqual(report["execution"]["answer_case_execution_count"], 10)
            self.assertEqual(report["execution"]["not_evaluable_count"], 6)

            serialized = json.dumps(report, sort_keys=True).casefold()
            self.assertNotIn('"prompt":', serialized)
            self.assertNotIn('"question":', serialized)
            self.assertNotIn('"answer":', serialized)
            self.assertNotIn('"conversation_id":', serialized)

            strict_status = main(
                [
                    "--repo-root",
                    str(ROOT),
                    "--output",
                    str(output_path),
                    "--strict",
                    "--generated-at-utc",
                    "2026-07-13T12:00:00Z",
                ]
            )
            self.assertEqual(strict_status, 1)

    def test_optional_human_review_packet_is_private_exact_and_blank(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "public-report.json"
            packet_path = Path(tmpdir) / "sensitive-human-review-packet.json"

            status = main(
                [
                    "--repo-root",
                    str(ROOT),
                    "--output",
                    str(output_path),
                    "--human-review-packet",
                    str(packet_path),
                    "--generated-at-utc",
                    "2026-07-14T17:15:00Z",
                ]
            )

            self.assertEqual(status, 0)
            public_report = json.loads(output_path.read_text(encoding="utf-8"))
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            self.assertEqual(
                packet["schema_version"], "final-answer-human-review-packet-v1"
            )
            self.assertEqual(packet["classification"], "sensitive-local-only")
            self.assertEqual(packet["commit_policy"], "do-not-commit")
            self.assertFalse(packet["contains_human_decisions"])
            self.assertEqual(packet["case_count"], 10)
            self.assertEqual(stat.S_IMODE(packet_path.stat().st_mode), 0o600)

            public_by_id = {
                item["case_id"]: item for item in public_report["case_results"]
            }
            for private_case in packet["cases"]:
                with self.subTest(case_id=private_case["case_id"]):
                    self.assertEqual(private_case["evaluation_surface"], "answer-path")
                    self.assertEqual(
                        private_case["execution_sha256"],
                        public_by_id[private_case["case_id"]]["execution_sha256"],
                    )
                    execution = private_case["execution"]
                    result_payload = execution["result"]
                    if result_payload is None:
                        result = None
                    else:
                        result = AnswerResult(
                            question=result_payload["question"],
                            normalized_question=result_payload["normalized_question"],
                            answer=result_payload["answer"],
                            model_identity=result_payload["model_identity"],
                            corpus_identity=result_payload["corpus_identity"],
                        )
                    reconstructed = CaseExecution(
                        case_id=private_case["case_id"],
                        result=result,
                        evidence=execution["evidence"],
                        error_type=execution["error_type"],
                    )
                    self.assertEqual(
                        fingerprint_case_execution(reconstructed),
                        private_case["execution_sha256"],
                    )
                    review_payload = {
                        key: private_case[key]
                        for key in (
                            "case_id",
                            "evaluation_surface",
                            "prompt",
                            "assertions",
                            "execution_sha256",
                            "execution",
                        )
                    }
                    review_payload_sha256 = hashlib.sha256(
                        json.dumps(
                            review_payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    ).hexdigest()
                    self.assertEqual(
                        private_case["review_payload_sha256"],
                        review_payload_sha256,
                    )
                    changed_prompt = json.loads(json.dumps(review_payload))
                    changed_prompt["prompt"] += " changed"
                    self.assertNotEqual(
                        hashlib.sha256(
                            json.dumps(
                                changed_prompt,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            ).encode("utf-8")
                        ).hexdigest(),
                        review_payload_sha256,
                    )
                    changed_assertions = json.loads(json.dumps(review_payload))
                    changed_assertions["assertions"][0]["assertion_id"] += "-changed"
                    self.assertNotEqual(
                        hashlib.sha256(
                            json.dumps(
                                changed_assertions,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            ).encode("utf-8")
                        ).hexdigest(),
                        review_payload_sha256,
                    )
                    template = private_case["blank_adjudication_template"]
                    self.assertEqual(
                        template["assessment_method"], "independent-human-review"
                    )
                    self.assertEqual(
                        template["evidence_binding"]["sha256"],
                        private_case["review_payload_sha256"],
                    )
                    self.assertEqual(
                        template["evidence_binding"]["execution_sha256"],
                        private_case["execution_sha256"],
                    )
                    self.assertEqual(
                        template["evidence_binding"]["kind"],
                        "answer-review-payload",
                    )
                    self.assertTrue(template["assertion_results"])
                    self.assertEqual(set(template["assertion_results"].values()), {None})

            public_serialized = json.dumps(public_report, sort_keys=True).casefold()
            private_serialized = json.dumps(packet, sort_keys=True).casefold()
            self.assertNotIn("human-review-packet", public_serialized)
            self.assertNotIn('"prompt":', public_serialized)
            self.assertIn('"prompt":', private_serialized)
            self.assertIn(
                "which danish language test is documented", private_serialized
            )

    def test_combined_evaluator_does_not_send_workflow_cases_to_answer_runner(self):
        class SurfaceAwareRunner:
            supported_evaluation_surfaces = {"answer-path"}
            public_identity = {
                "provider_id": "test",
                "model": "test",
                "corpus_id": "test",
            }

            def __init__(self):
                self.case_ids = []

            def run(self, case):
                self.case_ids.append(case["id"])
                return CaseExecution(
                    case_id=case["id"],
                    result=None,
                    evidence=[],
                    error_type="DeliberateAnswerPathTestError",
                )

        runner = SurfaceAwareRunner()
        report = generate_final_answer_evaluation(
            ROOT,
            runner=runner,
            mode="controlled",
            generated_at_utc="2026-07-14T12:00:00Z",
        )

        self.assertEqual(len(runner.case_ids), 10)
        by_id = {item["case_id"]: item for item in report["case_results"]}
        self.assertEqual(by_id["eval-001-permanent-language-supported"]["status"], "failed")
        self.assertEqual(
            by_id["eval-001-permanent-language-supported"]["checks"][
                "required_facts"
            ]["status"],
            "not_evaluable",
        )
        self.assertEqual(
            by_id["eval-001-permanent-language-supported"]["checks"][
                "required_facts"
            ]["expectation_count"],
            3,
        )
        self.assertEqual(
            by_id["eval-016-keyboard-evidence-drawer"]["status"],
            "not_evaluable",
        )
        self.assertEqual(
            by_id["eval-016-keyboard-evidence-drawer"]["evaluation_surface"],
            "browser-workflow",
        )
        self.assertFalse(
            by_id["eval-016-keyboard-evidence-drawer"]["generation_completed"]
        )
        self.assertIsNone(
            by_id["eval-016-keyboard-evidence-drawer"]["error_type"]
        )
        self.assertEqual(
            report["metrics"]["evaluation_surface_completion"]["status"],
            "not_evaluable",
        )


if __name__ == "__main__":
    unittest.main()
