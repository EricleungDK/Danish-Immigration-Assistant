"""Final-answer quality evaluation over the approved synthetic case set.

The report intentionally contains case identifiers and aggregate observations, not
question text, generated answer text, or conversation records.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence
from urllib.parse import urlparse

from .answer_pipeline import AnswerService, LocalProviderAnswerGenerator
from .embedding_provider import EmbeddingProvider
from .evidence_integrity import (
    canonical_json_sha256 as _sha256_json,
    sha256_file as _sha256_file,
    utc_now_seconds as _utc_now,
)
from .evaluation_machine_evidence import (
    MachineEvidenceError,
    build_automated_workflow_evidence,
    run_source_policy_scenario,
    validate_automated_workflow_artifact,
)
from .evaluation_quality_bar import (
    evaluation_case_assertion_specs,
    load_evaluation_cases,
    load_evaluation_quality_bar,
    validate_evaluation_cases,
)
from .knowledge_release import (
    BUNDLED_MINIMAL_RELEASE,
    default_data_dir,
    verify_knowledge_release,
)
from .provider_setup import (
    ProviderConfiguration,
    default_config_path,
    load_provider_configuration,
)
from .retrieval import HybridRetriever


SCHEMA_VERSION = "final-answer-evaluation-v1"
CASE_ADJUDICATION_SCHEMA_VERSION = "final-answer-case-adjudication-v1"
ADJUDICATION_BUNDLE_SCHEMA_VERSION = "final-answer-adjudications-v1"
WORKFLOW_EVIDENCE_SCHEMA_VERSION = "workflow-evaluation-evidence-v1"
HUMAN_REVIEW_PACKET_SCHEMA_VERSION = "final-answer-human-review-packet-v1"
WORKFLOW_SURFACES = {
    "browser-workflow",
    "knowledge-release-workflow",
    "provider-recovery-workflow",
}
SOURCE_POLICY_SURFACE = "source-policy-scenario"
DEFAULT_QUALITY_BAR_PATH = Path("config/evaluation-quality-bar.json")


class FinalAnswerEvaluationError(RuntimeError):
    """Raised when the evaluation harness cannot produce trustworthy evidence."""


@dataclass(frozen=True)
class CaseExecution:
    """Private in-memory result; answer content is never copied into the report."""

    case_id: str
    result: Any | None
    evidence: list[dict[str, Any]]
    error_type: str = ""


class FinalAnswerCaseRunner(Protocol):
    @property
    def supported_evaluation_surfaces(self) -> set[str]:
        ...

    @property
    def public_identity(self) -> dict[str, Any]:
        ...

    def run(self, case: dict[str, Any]) -> CaseExecution:
        ...


class ControlledAnswerGenerator:
    """Deterministic generator that copies only retrieved evidence into claims."""

    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        del question, normalized_question, configuration, schema
        return {
            "summary": "See the cited official facts below.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": str(item["content"]),
                    "citation_ids": [str(item["citation_id"])],
                }
                for item in evidence
            ],
        }


class _RecordingRetriever:
    def __init__(self, retriever: Any) -> None:
        self._retriever = retriever
        self.manifest = retriever.manifest
        self.last_evidence: list[dict[str, Any]] = []

    def retrieve(self, question: str) -> list[dict[str, Any]]:
        self.last_evidence = self._retriever.retrieve(question)
        return self.last_evidence


class AnswerServiceCaseRunner:
    """Evaluation adapter around the production AnswerService public seam."""

    def __init__(
        self,
        *,
        retriever: Any,
        generator: Any,
        configuration: ProviderConfiguration,
    ) -> None:
        self._retriever = _RecordingRetriever(retriever)
        self._service = AnswerService(
            retriever=self._retriever,
            generator=generator,
        )
        self._configuration = configuration

    @property
    def supported_evaluation_surfaces(self) -> set[str]:
        return {"answer-path"}

    @property
    def public_identity(self) -> dict[str, Any]:
        return {
            "provider_id": self._configuration.provider_id,
            "provider_version": self._configuration.provider_version,
            "model": self._configuration.model,
            "model_identity": self._configuration.model_identity,
            "corpus_id": str(self._retriever.manifest["corpus_id"]),
        }

    def run(self, case: dict[str, Any]) -> CaseExecution:
        self._retriever.last_evidence = []
        try:
            result = self._service.answer(
                str(case["prompt"]),
                self._configuration,
            )
        except Exception as exc:
            return CaseExecution(
                case_id=str(case["id"]),
                result=None,
                evidence=list(self._retriever.last_evidence),
                error_type=type(exc).__name__,
            )
        return CaseExecution(
            case_id=str(case["id"]),
            result=result,
            evidence=list(self._retriever.last_evidence),
        )


def build_live_ollama_runner(
    *,
    data_dir: str | Path,
    configuration: ProviderConfiguration,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_endpoint: str | None = None,
    generation_timeout_seconds: float = 90.0,
) -> AnswerServiceCaseRunner:
    """Build the live evaluator over the installed production retrieval path.

    The caller supplies a previously validated generation-provider configuration.
    HybridRetriever verifies the active corpus/index and uses the production Ollama
    embedding adapter unless an explicit provider is injected by a test.
    """

    if configuration.provider_id != "ollama":
        raise FinalAnswerEvaluationError(
            "live-ollama mode requires an Ollama generation-provider configuration"
        )
    retriever = HybridRetriever.from_data_dir(
        data_dir,
        embedding_provider=embedding_provider,
        embedding_endpoint=embedding_endpoint or configuration.endpoint,
    )
    return AnswerServiceCaseRunner(
        retriever=retriever,
        generator=LocalProviderAnswerGenerator(
            timeout_seconds=generation_timeout_seconds
        ),
        configuration=configuration,
    )


class _ControlledRetriever:
    """Deterministic evidence supplier; its results never count as retrieval metrics."""

    def __init__(
        self,
        *,
        manifest: dict[str, Any],
        documents: list[dict[str, Any]],
    ) -> None:
        self.manifest = manifest
        self._documents = documents

    def retrieve(self, question: str) -> list[dict[str, Any]]:
        query_tokens = set(_content_tokens(question))
        ranked = sorted(
            self._documents,
            key=lambda item: (
                -len(query_tokens.intersection(_content_tokens(_document_search_text(item)))),
                str(item["document_id"]),
            ),
        )
        results = []
        for document in ranked[:3]:
            result = dict(document)
            result["citation_id"] = str(document["document_id"])
            result["corpus_identity"] = str(self.manifest["corpus_id"])
            result["knowledge_release_id"] = str(
                self.manifest["knowledge_release_id"]
            )
            results.append(result)
        return results


def evaluate_final_answer_case(
    case: dict[str, Any],
    execution: CaseExecution,
    *,
    adjudication: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one case without returning its prompt, answer, or evidence text."""

    expectations = case["final_answer_expectations"]
    execution_sha256 = fingerprint_case_execution(execution)
    if execution.error_type or execution.result is None:
        failure = _not_evaluable_surface_case(
            case,
            reason=(
                "The answer execution failed before its approved assertions could "
                "be evaluated."
            ),
        )
        failure.update(
            {
                "status": "failed",
                "evaluation_completed": True,
                "error_type": execution.error_type or "MissingCaseResult",
                "retrieved_evidence_count": len(execution.evidence),
                "execution_sha256": execution_sha256,
            }
        )
        return failure

    payload = execution.result.answer
    if adjudication is not None:
        _validate_answer_adjudication(
            case,
            execution=execution,
            adjudication=adjudication,
        )
    sections = [item for item in payload.get("sections", []) if isinstance(item, dict)]
    official_fact_entries = [
        (index, item)
        for index, item in enumerate(sections, start=1)
        if item.get("kind") == "official_fact"
    ]
    official_facts = [item for _index, item in official_fact_entries]
    refusals = [item for item in sections if item.get("kind") == "refusal"]
    observed_behavior = _observed_behavior(payload, official_facts, refusals)
    expected_behavior = str(expectations["expected_behavior"])
    behavior = {
        "status": "passed" if observed_behavior == expected_behavior else "failed",
        "expected": expected_behavior,
        "observed": observed_behavior,
    }

    evidence_by_id = {
        str(item.get("citation_id")): item
        for item in execution.evidence
        if item.get("citation_id") is not None
    }
    fact_citation_count = 0
    cited_fact_count = 0
    relation_passed = 0
    relation_failed = 0
    relation_not_evaluable = 0
    supported_fact_count = 0
    unsupported_fact_count = 0
    support_not_evaluable_count = 0
    claim_support_adjudication = dict((adjudication or {}).get("claim_support") or {})
    for section_index, fact in official_fact_entries:
        citation_ids = [str(item) for item in fact.get("citation_ids", [])]
        if citation_ids:
            cited_fact_count += 1
        fact_citation_count += len(citation_ids)
        relation_statuses: list[str] = []
        for citation_id in citation_ids:
            evidence = evidence_by_id.get(citation_id)
            adjudicated_support = (
                claim_support_adjudication.get(f"section-{section_index}", {})
            ).get(citation_id)
            if evidence is None or not _eligible_material_source(evidence):
                relation_statuses.append("failed")
                relation_failed += 1
            elif adjudicated_support is True:
                relation_statuses.append("passed")
                relation_passed += 1
            elif adjudicated_support is False:
                relation_statuses.append("failed")
                relation_failed += 1
            elif _exact_claim_support(str(fact.get("text", "")), evidence):
                relation_statuses.append("passed")
                relation_passed += 1
            else:
                relation_statuses.append("not_evaluable")
                relation_not_evaluable += 1
        if "passed" in relation_statuses:
            supported_fact_count += 1
        elif "not_evaluable" in relation_statuses:
            support_not_evaluable_count += 1
        else:
            unsupported_fact_count += 1

    if not official_facts:
        citation_coverage = {
            "status": "not_applicable",
            "official_fact_count": 0,
            "cited_official_fact_count": 0,
            "value": None,
        }
    else:
        coverage_value = cited_fact_count / len(official_facts)
        citation_coverage = {
            "status": "passed" if coverage_value == 1.0 else "failed",
            "official_fact_count": len(official_facts),
            "cited_official_fact_count": cited_fact_count,
            "value": coverage_value,
        }

    if relation_failed:
        citation_correctness_status = "failed"
    elif relation_not_evaluable:
        citation_correctness_status = "not_evaluable"
    elif fact_citation_count:
        citation_correctness_status = "passed"
    else:
        citation_correctness_status = "not_applicable"
    citation_correctness = {
        "status": citation_correctness_status,
        "relation_count": fact_citation_count,
        "supported_relation_count": relation_passed,
        "incorrect_relation_count": relation_failed,
        "not_evaluable_relation_count": relation_not_evaluable,
        "value": (
            relation_passed / fact_citation_count if fact_citation_count else None
        ),
        "support_method": "normalized-exact-claim-in-cited-evidence",
    }

    if support_not_evaluable_count:
        unsupported_status = "not_evaluable"
    else:
        unsupported_status = "passed" if unsupported_fact_count == 0 else "failed"
    unsupported_claims = {
        "status": unsupported_status,
        "audited_official_fact_count": len(official_facts),
        "supported_count": supported_fact_count,
        "unsupported_count": unsupported_fact_count,
        "not_evaluable_count": support_not_evaluable_count,
        "value": (
            unsupported_fact_count / len(official_facts)
            if official_facts and not support_not_evaluable_count
            else None
        ),
    }

    conclusion_count = _personal_eligibility_conclusion_count(sections)
    personal_eligibility = {
        "status": "passed" if conclusion_count == 0 else "failed",
        "count": conclusion_count,
    }

    material_ids = {
        str(citation_id)
        for section in sections
        for citation_id in section.get("citation_ids", [])
    }
    material_evidence = [
        evidence_by_id[citation_id]
        for citation_id in sorted(material_ids)
        if citation_id in evidence_by_id
    ]
    expected_confidence = _expected_evidence_confidence(
        official_fact_count=len(official_facts),
        cited_official_fact_count=cited_fact_count,
        material_evidence=material_evidence,
    )
    observed_confidence = str(
        payload.get("trust", {}).get("evidence_confidence", "")
    )
    explicit_confidence = _explicit_trust_value(
        expectations.get("trust_indicators", []), "Evidence Confidence"
    )
    confidence_matches_dataset = (
        explicit_confidence is None or observed_confidence == explicit_confidence
    )
    evidence_confidence = {
        "status": (
            "passed" if observed_confidence == expected_confidence else "failed"
        ),
        "expected_from_evidence": expected_confidence,
        "expected_by_dataset": explicit_confidence,
        "observed": observed_confidence or None,
        "dataset_expectation_matches": confidence_matches_dataset,
    }

    expected_freshness = _expected_fresh_tomato(material_evidence)
    observed_freshness = str(
        payload.get("trust", {}).get("fresh_tomato_score", "")
    )
    explicit_freshness = _explicit_trust_value(
        expectations.get("trust_indicators", []), "Fresh Tomato Score"
    )
    freshness_matches_dataset = (
        explicit_freshness is None or observed_freshness == explicit_freshness
    )
    fresh_tomato = {
        "status": (
            "passed" if observed_freshness == expected_freshness else "failed"
        ),
        "expected_minimum_material_source_score": expected_freshness,
        "expected_by_dataset": explicit_freshness,
        "observed": observed_freshness or None,
        "material_source_count": len(material_evidence),
        "derived_independently_from_evidence_confidence": True,
        "dataset_expectation_matches": freshness_matches_dataset,
    }

    indicator_expectations = [
        str(item) for item in expectations.get("trust_indicators", [])
    ]
    expects_confidence = any(
        item.startswith("Evidence Confidence") for item in indicator_expectations
    )
    expects_freshness = any(
        item.startswith("Fresh Tomato Score") for item in indicator_expectations
    )
    trust_indicator_correctness = {
        "status": (
            "passed"
            if evidence_confidence["status"] == "passed"
            and fresh_tomato["status"] == "passed"
            and confidence_matches_dataset
            and freshness_matches_dataset
            and (not expects_confidence or bool(observed_confidence))
            and (not expects_freshness or bool(observed_freshness))
            else "failed"
        ),
        "evidence_confidence_required": expects_confidence,
        "fresh_tomato_score_required": expects_freshness,
    }

    cited_domains = sorted(
        {
            domain
            for item in material_evidence
            if (domain := _source_domain(str(item.get("official_url", ""))))
        }
    )
    required_domains = sorted(
        {_normalize_domain(item) for item in expectations["required_citation_domains"]}
    )
    forbidden_domains = sorted(
        {_normalize_domain(item) for item in expectations["forbidden_source_domains"]}
    )
    missing_domains = sorted(set(required_domains) - set(cited_domains))
    violating_domains = sorted(set(forbidden_domains).intersection(cited_domains))
    required_source_domains = {
        "status": "passed" if not missing_domains else "failed",
        "required": required_domains,
        "observed": cited_domains,
        "missing": missing_domains,
    }
    forbidden_source_domains = {
        "status": "passed" if not violating_domains else "failed",
        "forbidden": forbidden_domains,
        "violations": violating_domains,
    }

    required_facts = _assertion_group_check(
        case,
        adjudication,
        group="required_facts",
        success_count_key="covered_count",
    )
    forbidden_claims = _assertion_group_check(
        case,
        adjudication,
        group="forbidden_claims",
        success_count_key="absence_count",
    )
    forbidden_claims["violation_count"] = forbidden_claims.pop("failed_count")
    privacy_requirements = _assertion_group_check(
        case,
        adjudication,
        group="privacy_requirements",
        success_count_key="compliant_count",
    )

    checks = {
        "behavior": behavior,
        "required_facts": required_facts,
        "forbidden_claims": forbidden_claims,
        "privacy_requirements": privacy_requirements,
        "official_fact_citation_coverage": citation_coverage,
        "citation_correctness": citation_correctness,
        "unsupported_claims": unsupported_claims,
        "personal_eligibility_conclusions": personal_eligibility,
        "evidence_confidence": evidence_confidence,
        "trust_indicator_correctness": trust_indicator_correctness,
        "fresh_tomato_min_material_source_rule": fresh_tomato,
        "required_source_domains": required_source_domains,
        "forbidden_source_domains": forbidden_source_domains,
    }
    statuses = [check["status"] for check in checks.values()]
    if "failed" in statuses:
        status = "failed"
    elif "not_evaluable" in statuses:
        status = "not_evaluable"
    else:
        status = "passed"
    return {
        "case_id": str(case["id"]),
        "status": status,
        "evaluation_surface": str(case.get("evaluation_surface", "answer-path")),
        "evaluation_completed": True,
        "generation_completed": True,
        "error_type": None,
        "retrieved_evidence_count": len(execution.evidence),
        "execution_sha256": execution_sha256,
        "checks": checks,
    }


def generate_final_answer_evaluation(
    repo_root: str | Path,
    *,
    runner: FinalAnswerCaseRunner,
    mode: str,
    generated_at_utc: str | None = None,
    adjudications: dict[str, Any] | None = None,
    human_review_packet_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the final-answer harness and return a content-free evidence report."""

    root = Path(repo_root)
    quality_bar_path = root / DEFAULT_QUALITY_BAR_PATH
    quality_bar = load_evaluation_quality_bar(quality_bar_path)
    dataset_path = root / quality_bar["evaluation_set"]["path"]
    dataset = load_evaluation_cases(dataset_path)
    failures = validate_evaluation_cases(quality_bar, dataset)
    if failures:
        raise FinalAnswerEvaluationError("; ".join(failures))

    dataset_sha256 = _sha256_file(dataset_path)
    adjudications_by_case = _validate_adjudication_bundle(
        dataset,
        dataset_sha256=dataset_sha256,
        adjudications=adjudications,
    )
    supported_surfaces = set(
        getattr(runner, "supported_evaluation_surfaces", {"answer-path"})
    )
    case_results: list[dict[str, Any]] = []
    answer_review_executions: list[tuple[dict[str, Any], CaseExecution]] = []
    executed_answer_case_count = 0
    for case in dataset["cases"]:
        surface = str(case["evaluation_surface"])
        adjudication = adjudications_by_case.get(str(case["id"]))
        if surface == SOURCE_POLICY_SURFACE:
            case_results.append(_evaluate_source_policy_case(case))
        elif surface in supported_surfaces:
            execution = runner.run(case)
            executed_answer_case_count += 1
            if surface == "answer-path":
                answer_review_executions.append((case, execution))
            case_results.append(
                evaluate_final_answer_case(
                    case,
                    execution,
                    adjudication=adjudication,
                )
            )
        elif surface in WORKFLOW_SURFACES:
            case_results.append(
                _evaluate_workflow_case(
                    case,
                    adjudication=adjudication,
                    repo_root=root,
                )
            )
        else:
            case_results.append(
                _not_evaluable_surface_case(
                    case,
                    reason=(
                        f"The selected runner does not implement the {surface!r} "
                        "evaluation surface."
                    ),
                )
            )
    error_count = sum(bool(result.get("error_type")) for result in case_results)
    metrics = _aggregate_metrics(
        case_results,
        thresholds=quality_bar["thresholds"]["final_answer"],
    )
    threshold_failures = [
        metric_id
        for metric_id, metric in metrics.items()
        if metric["status"] != "passed"
    ]
    assertion_specs = [
        spec
        for case in dataset["cases"]
        for spec in evaluation_case_assertion_specs(case)
    ]
    assertion_group_counts = {
        group: sum(spec["expectation_group"] == group for spec in assertion_specs)
        for group in (
            "required_facts",
            "forbidden_claims",
            "trust_indicators",
            "privacy_requirements",
        )
    }

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc or _utc_now(),
        "dataset": {
            "dataset_id": dataset["dataset_id"],
            "version": dataset["version"],
            "case_count": len(dataset["cases"]),
            "sha256": dataset_sha256,
            "uses_production_user_conversation_data": False,
        },
        "quality_bar": {
            "quality_bar_id": quality_bar["quality_bar_id"],
            "version": quality_bar["version"],
            "sha256": _sha256_file(quality_bar_path),
        },
        "retrieval_and_final_answer_evaluation_separate": bool(
            quality_bar["evaluation_set"][
                "retrieval_and_final_answer_evaluation_separate"
            ]
        ),
        "assertion_contract": {
            "schema_version": dataset["assertion_contract"]["schema_version"],
            "adjudication_schema_version": dataset["assertion_contract"][
                "adjudication_schema_version"
            ],
            "assertion_count": len(assertion_specs),
            "expectation_group_counts": assertion_group_counts,
            "semantic_passes_require_independent_adjudication": True,
            "workflow_passes_require_hash_verified_evidence": True,
        },
        "adjudications": {
            "provided": adjudications is not None,
            "case_count": len(adjudications_by_case),
            "sha256": _sha256_json(adjudications) if adjudications is not None else None,
            "automated_workflow_case_count": sum(
                record.get("assessment_method") == "automated-workflow-test"
                for record in adjudications_by_case.values()
            ),
            "independent_human_case_count": sum(
                record.get("assessment_method") == "independent-human-review"
                for record in adjudications_by_case.values()
            ),
        },
        "execution": {
            "mode": mode,
            "case_count": len(case_results),
            "completed_count": sum(
                bool(result.get("evaluation_completed")) for result in case_results
            ),
            "not_evaluable_count": sum(
                not bool(result.get("evaluation_completed")) for result in case_results
            ),
            "answer_case_execution_count": executed_answer_case_count,
            "error_count": error_count,
            "live_provider_calls": mode == "live-ollama",
        },
        "identity": runner.public_identity,
        "case_results": case_results,
        "metrics": metrics,
        "dataset_limitations": [
            {
                "fields": [
                    "final_answer_expectations.required_facts",
                    "final_answer_expectations.forbidden_claims",
                ],
                "reason": (
                    "Prose expectations cannot establish semantic coverage or absence "
                    "without an independent adjudication record."
                ),
            },
            {
                "fields": [
                    "final_answer_expectations.trust_indicators",
                    "final_answer_expectations.privacy_requirements",
                ],
                "reason": (
                    "Browser, update, identity, and recovery workflow passes require "
                    "assertion-specific results bound to a verified workflow artifact."
                ),
            },
        ],
        "threshold_failures": threshold_failures,
        "strict_passed": not threshold_failures,
        "privacy_assertions": {
            "contains_case_prompts": False,
            "contains_generated_answer_text": False,
            "contains_conversation_records": False,
            "contains_conversation_identifiers": False,
        },
    }
    if human_review_packet_path is not None:
        _write_human_review_packet(
            repo_root=root,
            output_path=human_review_packet_path,
            dataset=dataset,
            dataset_sha256=dataset_sha256,
            generated_at_utc=report["generated_at_utc"],
            executions=answer_review_executions,
        )
    return report


def _validate_adjudication_bundle(
    dataset: dict[str, Any],
    *,
    dataset_sha256: str,
    adjudications: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if adjudications is None:
        return {}
    if adjudications.get("schema_version") != ADJUDICATION_BUNDLE_SCHEMA_VERSION:
        raise FinalAnswerEvaluationError("adjudication bundle has an unsupported schema")
    binding = adjudications.get("dataset")
    expected_binding = {
        "dataset_id": dataset["dataset_id"],
        "version": dataset["version"],
        "sha256": dataset_sha256,
    }
    if binding != expected_binding:
        raise FinalAnswerEvaluationError(
            "adjudication bundle does not match the exact approved evaluation dataset"
        )
    records = adjudications.get("cases")
    if not isinstance(records, list):
        raise FinalAnswerEvaluationError("adjudication bundle cases must be a list")
    known_case_ids = {str(case["id"]) for case in dataset["cases"]}
    by_case: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise FinalAnswerEvaluationError(
                "each adjudication bundle case must be an object"
            )
        case_id = str(record.get("case_id", ""))
        if case_id not in known_case_ids:
            raise FinalAnswerEvaluationError(
                f"adjudication bundle references unknown case {case_id!r}"
            )
        if case_id in by_case:
            raise FinalAnswerEvaluationError(
                f"adjudication bundle duplicates case {case_id!r}"
            )
        by_case[case_id] = record
    return by_case


def _merge_adjudication_bundles(
    existing: dict[str, Any] | None,
    automated: dict[str, Any],
) -> dict[str, Any]:
    """Merge machine workflow evidence without overwriting a reviewed record."""

    if existing is None:
        return automated
    if existing.get("schema_version") != ADJUDICATION_BUNDLE_SCHEMA_VERSION:
        raise FinalAnswerEvaluationError("adjudication bundle has an unsupported schema")
    if existing.get("dataset") != automated.get("dataset"):
        raise FinalAnswerEvaluationError(
            "automated evidence and adjudications target different evaluation datasets"
        )
    existing_cases = existing.get("cases")
    automated_cases = automated.get("cases")
    if not isinstance(existing_cases, list) or not isinstance(automated_cases, list):
        raise FinalAnswerEvaluationError("adjudication bundle cases must be a list")
    existing_ids = {
        str(record.get("case_id", ""))
        for record in existing_cases
        if isinstance(record, dict)
    }
    automated_ids = {
        str(record.get("case_id", ""))
        for record in automated_cases
        if isinstance(record, dict)
    }
    overlap = sorted(existing_ids.intersection(automated_ids))
    if overlap:
        raise FinalAnswerEvaluationError(
            "automated workflow evidence would overwrite existing adjudications: "
            f"{', '.join(overlap)}"
        )
    merged = {
        "schema_version": ADJUDICATION_BUNDLE_SCHEMA_VERSION,
        "dataset": dict(automated["dataset"]),
        "cases": [*existing_cases, *automated_cases],
        "contains_human_assessment": any(
            isinstance(record, dict)
            and record.get("assessment_method") == "independent-human-review"
            for record in existing_cases
        ),
        "automated_workflow_evidence_included": True,
    }
    return merged


def _evaluate_source_policy_case(case: dict[str, Any]) -> dict[str, Any]:
    """Score a deterministic production-policy scenario without human semantics."""

    try:
        scenario = run_source_policy_scenario(case)
    except MachineEvidenceError as exc:
        raise FinalAnswerEvaluationError(str(exc)) from exc
    expected_behavior = str(case["final_answer_expectations"]["expected_behavior"])
    observed_behavior = str(scenario["observed_behavior"])
    behavior = {
        "status": "passed" if observed_behavior == expected_behavior else "failed",
        "expected": expected_behavior,
        "observed": observed_behavior,
    }
    required_facts = _assertion_group_check(
        case,
        scenario,
        group="required_facts",
        success_count_key="covered_count",
    )
    forbidden_claims = _assertion_group_check(
        case,
        scenario,
        group="forbidden_claims",
        success_count_key="absence_count",
    )
    forbidden_claims["violation_count"] = forbidden_claims.pop("failed_count")
    privacy_requirements = _assertion_group_check(
        case,
        scenario,
        group="privacy_requirements",
        success_count_key="compliant_count",
    )
    trust_indicators = _assertion_group_check(
        case,
        scenario,
        group="trust_indicators",
        success_count_key="correct_count",
    )
    checks = {
        "behavior": behavior,
        "required_facts": required_facts,
        "forbidden_claims": forbidden_claims,
        "privacy_requirements": privacy_requirements,
        "trust_indicator_correctness": trust_indicators,
        **dict(scenario["metric_checks"]),
    }
    scored_statuses = [
        behavior["status"],
        required_facts["status"],
        forbidden_claims["status"],
        privacy_requirements["status"],
        trust_indicators["status"],
        *[item["status"] for item in scenario["metric_checks"].values()],
    ]
    status = "failed" if "failed" in scored_statuses else "passed"
    return {
        "case_id": str(case["id"]),
        "status": status,
        "evaluation_surface": SOURCE_POLICY_SURFACE,
        "evaluation_completed": True,
        "generation_completed": bool(
            scenario["source_policy"]["generator_call_count"]
        ),
        "error_type": None,
        "retrieved_evidence_count": scenario["retrieved_evidence_count"],
        "assessment_method": scenario["assessment_method"],
        "execution_sha256": scenario["execution_sha256"],
        "source_policy": scenario["source_policy"],
        "checks": checks,
    }


def _evaluate_workflow_case(
    case: dict[str, Any],
    *,
    adjudication: dict[str, Any] | None,
    repo_root: Path,
) -> dict[str, Any]:
    if adjudication is None:
        return _not_evaluable_surface_case(
            case,
            reason=(
                "This workflow requires a hash-verified evidence artifact and "
                "evidence-bound assertion results."
            ),
            workflow=True,
        )

    case_id = str(case["id"])
    _validate_case_adjudication_common(case, adjudication)
    method = adjudication.get("assessment_method")
    if method != "automated-workflow-test":
        raise FinalAnswerEvaluationError(
            f"case {case_id} workflow adjudication requires automated-workflow-test"
        )
    binding = adjudication.get("evidence_binding")
    if not isinstance(binding, dict) or binding.get("kind") != "workflow-artifact":
        raise FinalAnswerEvaluationError(
            f"case {case_id} workflow adjudication requires a workflow-artifact binding"
        )
    artifact_value = binding.get("path")
    if not isinstance(artifact_value, str) or not artifact_value.strip():
        raise FinalAnswerEvaluationError(
            f"case {case_id} workflow evidence path is missing"
        )
    artifact_path = Path(artifact_value)
    if not artifact_path.is_absolute():
        artifact_path = repo_root / artifact_path
    if not artifact_path.is_file():
        raise FinalAnswerEvaluationError(
            f"case {case_id} workflow evidence artifact does not exist"
        )
    artifact_sha256 = _sha256_file(artifact_path)
    if binding.get("sha256") != artifact_sha256:
        raise FinalAnswerEvaluationError(
            f"case {case_id} workflow evidence SHA-256 does not match the artifact"
        )
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalAnswerEvaluationError(
            f"case {case_id} workflow evidence artifact is not valid JSON"
        ) from exc
    if not isinstance(artifact, dict):
        raise FinalAnswerEvaluationError(
            f"case {case_id} workflow evidence artifact must be an object"
        )
    if artifact.get("schema_version") != WORKFLOW_EVIDENCE_SCHEMA_VERSION:
        raise FinalAnswerEvaluationError(
            f"case {case_id} workflow evidence has an unsupported schema"
        )
    if artifact.get("case_id") != case_id:
        raise FinalAnswerEvaluationError(
            f"case {case_id} workflow evidence is bound to a different case"
        )
    if artifact.get("evaluation_surface") != case["evaluation_surface"]:
        raise FinalAnswerEvaluationError(
            f"case {case_id} workflow evidence has the wrong evaluation surface"
        )
    _validate_workflow_artifact_sources(
        case,
        artifact=artifact,
        repo_root=repo_root,
        automated=method == "automated-workflow-test",
    )
    if method == "automated-workflow-test" and artifact.get("exit_status") != 0:
        raise FinalAnswerEvaluationError(
            f"case {case_id} automated workflow evidence did not pass"
        )

    if artifact.get("assertion_results") != adjudication.get("assertion_results"):
        raise FinalAnswerEvaluationError(
            f"case {case_id} assertion results do not match workflow evidence"
        )
    expected_behavior = str(case["final_answer_expectations"]["expected_behavior"])
    behavior = {
        "status": "not_applicable",
        "expected": expected_behavior,
        "observed": None,
        "reason": (
            "Clarify/answer/refuse classification applies to answer-producing "
            "surfaces, not non-answer workflow evidence."
        ),
    }
    required_facts = _assertion_group_check(
        case,
        adjudication,
        group="required_facts",
        success_count_key="covered_count",
    )
    forbidden_claims = _assertion_group_check(
        case,
        adjudication,
        group="forbidden_claims",
        success_count_key="absence_count",
    )
    forbidden_claims["violation_count"] = forbidden_claims.pop("failed_count")
    privacy_requirements = _assertion_group_check(
        case,
        adjudication,
        group="privacy_requirements",
        success_count_key="compliant_count",
    )
    trust_indicators = _assertion_group_check(
        case,
        adjudication,
        group="trust_indicators",
        success_count_key="correct_count",
    )
    checks = {
        "behavior": behavior,
        "required_facts": required_facts,
        "forbidden_claims": forbidden_claims,
        "privacy_requirements": privacy_requirements,
        "trust_indicator_correctness": trust_indicators,
        **_non_answer_metric_checks("not_applicable"),
    }
    scored_statuses = [
        required_facts["status"],
        forbidden_claims["status"],
        privacy_requirements["status"],
        trust_indicators["status"],
    ]
    if "failed" in scored_statuses:
        status = "failed"
    elif "not_evaluable" in scored_statuses:
        status = "not_evaluable"
    else:
        status = "passed"
    return {
        "case_id": case_id,
        "status": status,
        "evaluation_surface": str(case["evaluation_surface"]),
        "evaluation_completed": True,
        "generation_completed": False,
        "error_type": None,
        "retrieved_evidence_count": 0,
        "assessment_method": method,
        "evidence_sha256": artifact_sha256,
        "checks": checks,
    }


def _validate_workflow_artifact_sources(
    case: dict[str, Any],
    *,
    artifact: dict[str, Any],
    repo_root: Path,
    automated: bool,
) -> None:
    """Recheck every nested proof source instead of trusting a derived artifact."""

    case_id = str(case["id"])
    sources = artifact.get("source_evidence")
    if not isinstance(sources, list) or not sources:
        raise FinalAnswerEvaluationError(
            f"case {case_id} workflow evidence is missing source evidence bindings"
        )
    known_source_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise FinalAnswerEvaluationError(
                f"case {case_id} workflow source evidence must be an object"
            )
        source_id = str(source.get("source_id", "")).strip()
        if not source_id or source_id in known_source_ids:
            raise FinalAnswerEvaluationError(
                f"case {case_id} workflow source evidence IDs must be unique"
            )
        known_source_ids.add(source_id)
        source_value = source.get("path")
        if not isinstance(source_value, str) or not source_value.strip():
            raise FinalAnswerEvaluationError(
                f"case {case_id} workflow source evidence path is missing"
            )
        source_path = Path(source_value)
        if not source_path.is_absolute():
            source_path = repo_root / source_path
        if not source_path.is_file():
            raise FinalAnswerEvaluationError(
                f"case {case_id} workflow source evidence does not exist"
            )
        if source.get("sha256") != _sha256_file(source_path):
            raise FinalAnswerEvaluationError(
                f"case {case_id} workflow source evidence SHA-256 does not match"
            )

    proofs = artifact.get("assertion_proofs")
    results = artifact.get("assertion_results")
    if not isinstance(proofs, dict) or not isinstance(results, dict):
        raise FinalAnswerEvaluationError(
            f"case {case_id} workflow evidence lacks assertion-specific proofs"
        )
    if set(proofs) != set(results):
        raise FinalAnswerEvaluationError(
            f"case {case_id} workflow proof IDs do not match assertion results"
        )
    for assertion_id, status in results.items():
        assertion_proofs = proofs.get(assertion_id)
        if status == "passed" and (
            not isinstance(assertion_proofs, list) or not assertion_proofs
        ):
            raise FinalAnswerEvaluationError(
                f"case {case_id} passed assertion lacks source proof: {assertion_id}"
            )
        for proof in assertion_proofs or []:
            if (
                not isinstance(proof, dict)
                or proof.get("source_id") not in known_source_ids
                or not isinstance(proof.get("observation_id"), str)
                or not proof["observation_id"].strip()
            ):
                raise FinalAnswerEvaluationError(
                    f"case {case_id} assertion proof is invalid: {assertion_id}"
                )
    if automated and artifact.get("contains_human_assessment") is not False:
        raise FinalAnswerEvaluationError(
            f"case {case_id} automated workflow evidence must disclaim human assessment"
        )
    if automated:
        try:
            validate_automated_workflow_artifact(
                case=case,
                artifact=artifact,
                repo_root=repo_root,
            )
        except MachineEvidenceError as exc:
            raise FinalAnswerEvaluationError(str(exc)) from exc


def _not_evaluable_surface_case(
    case: dict[str, Any],
    *,
    reason: str,
    workflow: bool = False,
) -> dict[str, Any]:
    required_facts = _assertion_group_check(
        case, None, group="required_facts", success_count_key="covered_count"
    )
    forbidden_claims = _assertion_group_check(
        case, None, group="forbidden_claims", success_count_key="absence_count"
    )
    forbidden_claims["violation_count"] = forbidden_claims.pop("failed_count")
    privacy_requirements = _assertion_group_check(
        case, None, group="privacy_requirements", success_count_key="compliant_count"
    )
    trust_indicators = _assertion_group_check(
        case, None, group="trust_indicators", success_count_key="correct_count"
    )
    metric_status = "not_applicable" if workflow else "not_evaluable"
    checks = {
        "behavior": {
            "status": "not_evaluable",
            "expected": case["final_answer_expectations"]["expected_behavior"],
            "observed": None,
            "reason": reason,
        },
        "required_facts": required_facts,
        "forbidden_claims": forbidden_claims,
        "privacy_requirements": privacy_requirements,
        "trust_indicator_correctness": trust_indicators,
        **_non_answer_metric_checks(metric_status),
    }
    return {
        "case_id": str(case["id"]),
        "status": "not_evaluable",
        "evaluation_surface": str(case["evaluation_surface"]),
        "evaluation_completed": False,
        "generation_completed": False,
        "error_type": None,
        "retrieved_evidence_count": 0,
        "not_evaluable_reason": reason,
        "checks": checks,
    }


def _non_answer_metric_checks(status: str) -> dict[str, dict[str, Any]]:
    return {
        "official_fact_citation_coverage": {
            "status": status,
            "official_fact_count": 0,
            "cited_official_fact_count": 0,
            "value": None,
        },
        "citation_correctness": {
            "status": status,
            "relation_count": 0,
            "supported_relation_count": 0,
            "incorrect_relation_count": 0,
            "not_evaluable_relation_count": 0,
            "value": None,
        },
        "unsupported_claims": {
            "status": status,
            "audited_official_fact_count": 0,
            "supported_count": 0,
            "unsupported_count": 0,
            "not_evaluable_count": 0,
            "value": None,
        },
        "personal_eligibility_conclusions": {"status": status, "count": 0},
        "evidence_confidence": {"status": status},
        "fresh_tomato_min_material_source_rule": {"status": status},
        "required_source_domains": {
            "status": status,
            "required": [],
            "observed": [],
            "missing": [],
        },
        "forbidden_source_domains": {
            "status": status,
            "forbidden": [],
            "violations": [],
        },
    }


def _write_human_review_packet(
    *,
    repo_root: Path,
    output_path: str | Path,
    dataset: dict[str, Any],
    dataset_sha256: str,
    generated_at_utc: str,
    executions: list[tuple[dict[str, Any], CaseExecution]],
) -> None:
    """Write exact synthetic executions separately for a real independent reviewer."""

    destination = Path(output_path).expanduser().resolve()
    resolved_root = repo_root.resolve()
    try:
        destination.relative_to(resolved_root)
    except ValueError:
        pass
    else:
        raise FinalAnswerEvaluationError(
            "human review packet is sensitive and must be written outside the repository"
        )

    cases: list[dict[str, Any]] = []
    for case, execution in executions:
        review_payload = _answer_review_payload(case, execution)
        execution_sha256 = str(review_payload["execution_sha256"])
        assertion_specs = review_payload["assertions"]
        review_payload_sha256 = _sha256_json(review_payload)
        claim_support: dict[str, dict[str, None]] = {}
        if execution.result is not None:
            for section_index, section in enumerate(
                execution.result.answer.get("sections", []),
                start=1,
            ):
                if not isinstance(section, dict) or section.get("kind") != "official_fact":
                    continue
                claim_support[f"section-{section_index}"] = {
                    str(citation_id): None
                    for citation_id in section.get("citation_ids", [])
                }
        cases.append(
            {
                **review_payload,
                "review_payload_sha256": review_payload_sha256,
                "blank_adjudication_template": {
                    "schema_version": CASE_ADJUDICATION_SCHEMA_VERSION,
                    "case_id": str(case["id"]),
                    "evaluation_surface": "answer-path",
                    "evidence_binding": {
                        "kind": "answer-review-payload",
                        "sha256": review_payload_sha256,
                        "execution_sha256": execution_sha256,
                    },
                    "assessment_method": "independent-human-review",
                    "assertion_results": {
                        spec["assertion_id"]: None for spec in assertion_specs
                    },
                    "claim_support": claim_support,
                },
            }
        )
    packet = {
        "schema_version": HUMAN_REVIEW_PACKET_SCHEMA_VERSION,
        "classification": "sensitive-local-only",
        "commit_policy": "do-not-commit",
        "contains_human_decisions": False,
        "uses_production_user_conversation_data": False,
        "generated_at_utc": generated_at_utc,
        "dataset": {
            "dataset_id": dataset["dataset_id"],
            "version": dataset["version"],
            "sha256": dataset_sha256,
        },
        "case_count": len(cases),
        "review_instructions": [
            (
                "Review each exact synthetic prompt, generated answer, and retrieved "
                "evidence without rerunning the model."
            ),
            (
                "Replace every null assertion result with passed, failed, or "
                "not_evaluable, and replace each null claim-support decision with "
                "true or false."
            ),
            (
                "Copy completed case templates into a final-answer-adjudications-v1 "
                "bundle bound to this dataset; do not commit this packet."
            ),
        ],
        "cases": cases,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(packet, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(destination)
    destination.chmod(0o600)


def write_final_answer_evaluation_report(
    report: dict[str, Any], output_path: str | Path
) -> None:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate final answers against the approved 20-case quality bar."
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--mode",
        choices=("controlled", "live-ollama"),
        default="controlled",
    )
    parser.add_argument("--data-dir", default=default_data_dir())
    parser.add_argument("--config-path", default=default_config_path())
    parser.add_argument(
        "--adjudications",
        help=(
            "Optional evidence-bound adjudication bundle for semantic and "
            "non-answer workflow assertions."
        ),
    )
    parser.add_argument(
        "--generate-automated-evidence",
        metavar="OUTPUT_DIR",
        help=(
            "Run or validate the six non-answer workflow executions, write their "
            "hash-bound artifacts to OUTPUT_DIR, and merge their machine-only "
            "adjudications into this evaluation."
        ),
    )
    parser.add_argument(
        "--release-monitor-report",
        default="docs/progress/release-monitors-live.json",
        help="Strict live release-monitor report used by update/privacy/rollback workflows.",
    )
    parser.add_argument(
        "--browser-workflow-report",
        help=(
            "Optional existing focused Playwright execution report. When omitted, "
            "the evaluator runs the focused browser workflow spec."
        ),
    )
    parser.add_argument(
        "--provider-recovery-report",
        help=(
            "Optional existing provider-recovery execution report. When omitted, "
            "the evaluator runs the in-process ASGI recovery workflow."
        ),
    )
    parser.add_argument(
        "--human-review-packet",
        metavar="LOCAL_PATH",
        help=(
            "Write a sensitive mode-0600 packet outside the repository with exact "
            "answer executions and blank independent-human-review templates. This "
            "packet is never embedded in the public report."
        ),
    )
    parser.add_argument("--generated-at-utc")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root)
    try:
        adjudications = None
        if args.adjudications:
            adjudications = json.loads(
                Path(args.adjudications).read_text(encoding="utf-8")
            )
            if not isinstance(adjudications, dict):
                raise FinalAnswerEvaluationError(
                    "adjudication bundle must be a JSON object"
                )
        if args.generate_automated_evidence:
            def resolve_input_path(value: str | None) -> Path | None:
                if value is None:
                    return None
                candidate = Path(value)
                return candidate if candidate.is_absolute() else repo_root / candidate

            automated = build_automated_workflow_evidence(
                repo_root=repo_root,
                output_dir=resolve_input_path(args.generate_automated_evidence),
                release_monitor_path=resolve_input_path(args.release_monitor_report),
                browser_workflow_report_path=resolve_input_path(
                    args.browser_workflow_report
                ),
                provider_recovery_report_path=resolve_input_path(
                    args.provider_recovery_report
                ),
                generated_at_utc=args.generated_at_utc,
            )
            adjudications = _merge_adjudication_bundles(
                adjudications,
                automated,
            )
        if args.mode == "live-ollama":
            configuration = load_provider_configuration(args.config_path)
            if configuration is None:
                raise FinalAnswerEvaluationError(
                    f"No validated provider configuration found at {args.config_path}"
                )
            runner = build_live_ollama_runner(
                data_dir=args.data_dir,
                configuration=configuration,
            )
        else:
            verified_release = verify_knowledge_release(BUNDLED_MINIMAL_RELEASE)
            runner = AnswerServiceCaseRunner(
                retriever=_ControlledRetriever(
                    manifest=verified_release["manifest"],
                    documents=verified_release["documents"],
                ),
                generator=ControlledAnswerGenerator(),
                configuration=ProviderConfiguration(
                    provider_id="controlled",
                    endpoint="in-process",
                    model="controlled-evidence-copy-v1",
                    provider_version="in-process-v1",
                    model_identity={"id": "controlled-evidence-copy-v1"},
                    capabilities=["generation"],
                    validated_at_utc=args.generated_at_utc or "deterministic-run",
                ),
            )
        report = generate_final_answer_evaluation(
            repo_root,
            runner=runner,
            mode=args.mode,
            generated_at_utc=args.generated_at_utc,
            adjudications=adjudications,
            human_review_packet_path=args.human_review_packet,
        )
        write_final_answer_evaluation_report(report, args.output)
    except Exception as exc:
        print(f"final-answer evaluation failed: {exc}", file=sys.stderr)
        return 2

    if args.strict and not report["strict_passed"]:
        return 1
    return 0


def _aggregate_metrics(
    case_results: list[dict[str, Any]],
    *,
    thresholds: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    checks = [result.get("checks", {}) for result in case_results]

    required_checks = [item.get("required_facts", {}) for item in checks]
    required_total = sum(item.get("expectation_count", 0) for item in required_checks)
    required_covered = sum(item.get("covered_count", 0) for item in required_checks)
    if any(item.get("status") == "not_evaluable" for item in required_checks):
        required_fact_coverage = {
            "status": "not_evaluable",
            "observed": None,
            "threshold": thresholds["required_fact_coverage_min"],
            "expectation_count": required_total,
            "reason": (
                "The dataset required_facts entries are prose instructions without "
                "machine-readable fact identifiers or complete adjudications."
            ),
        }
    else:
        required_value = required_covered / required_total if required_total else 1.0
        required_fact_coverage = {
            "status": (
                "passed"
                if required_value >= thresholds["required_fact_coverage_min"]
                else "failed"
            ),
            "observed": required_value,
            "threshold": thresholds["required_fact_coverage_min"],
            "covered_count": required_covered,
            "expectation_count": required_total,
        }

    forbidden_checks = [item.get("forbidden_claims", {}) for item in checks]
    forbidden_total = sum(item.get("expectation_count", 0) for item in forbidden_checks)
    forbidden_violations = sum(item.get("violation_count", 0) for item in forbidden_checks)
    if any(item.get("status") == "not_evaluable" for item in forbidden_checks):
        forbidden_claims = {
            "status": "not_evaluable",
            "observed": None,
            "threshold": thresholds["forbidden_claims_max"],
            "expectation_count": forbidden_total,
            "reason": (
                "The dataset forbidden_claims entries are prose instructions without "
                "machine-readable matchers or complete adjudications."
            ),
        }
    else:
        forbidden_claims = {
            "status": (
                "passed"
                if forbidden_violations <= thresholds["forbidden_claims_max"]
                else "failed"
            ),
            "observed": forbidden_violations,
            "threshold": thresholds["forbidden_claims_max"],
            "expectation_count": forbidden_total,
        }

    citation_coverage_checks = [
        item.get("official_fact_citation_coverage", {}) for item in checks
    ]
    official_fact_count = sum(
        item.get("official_fact_count", 0) for item in citation_coverage_checks
    )
    cited_fact_count = sum(
        item.get("cited_official_fact_count", 0) for item in citation_coverage_checks
    )
    citation_coverage_value = (
        cited_fact_count / official_fact_count if official_fact_count else None
    )
    citation_coverage_unknown = any(
        item.get("status") == "not_evaluable" for item in citation_coverage_checks
    )
    citation_coverage_failed = any(
        item.get("status") == "failed" for item in citation_coverage_checks
    )
    official_fact_citation_coverage = {
        "status": (
            "failed"
            if citation_coverage_failed
            or (
                citation_coverage_value is not None
                and citation_coverage_value
                < thresholds["official_fact_citation_coverage_min"]
            )
            else "not_evaluable"
            if citation_coverage_unknown or citation_coverage_value is None
            else "passed"
        ),
        "observed": citation_coverage_value,
        "threshold": thresholds["official_fact_citation_coverage_min"],
        "official_fact_count": official_fact_count,
        "cited_official_fact_count": cited_fact_count,
    }

    correctness_checks = [item.get("citation_correctness", {}) for item in checks]
    relation_count = sum(item.get("relation_count", 0) for item in correctness_checks)
    supported_relations = sum(
        item.get("supported_relation_count", 0) for item in correctness_checks
    )
    incorrect_relations = sum(
        item.get("incorrect_relation_count", 0) for item in correctness_checks
    )
    unknown_relations = sum(
        item.get("not_evaluable_relation_count", 0) for item in correctness_checks
    )
    correctness_unknown = unknown_relations or any(
        item.get("status") == "not_evaluable" for item in correctness_checks
    )
    correctness_value = supported_relations / relation_count if relation_count else None
    if incorrect_relations or any(
        item.get("status") == "failed" for item in correctness_checks
    ):
        correctness_status = "failed"
    elif correctness_unknown or correctness_value is None:
        correctness_status = "not_evaluable"
    elif correctness_value >= thresholds["citation_correctness_min"]:
        correctness_status = "passed"
    else:
        correctness_status = "failed"
    citation_correctness = {
        "status": correctness_status,
        "observed": correctness_value if not correctness_unknown else None,
        "threshold": thresholds["citation_correctness_min"],
        "relation_count": relation_count,
        "supported_relation_count": supported_relations,
        "incorrect_relation_count": incorrect_relations,
        "not_evaluable_relation_count": unknown_relations,
    }
    if correctness_unknown:
        citation_correctness["reason"] = (
            "Some paraphrased claim-to-evidence relationships lack independent "
            "adjudication; exact support was not invented."
        )

    unsupported_checks = [item.get("unsupported_claims", {}) for item in checks]
    audited_claim_count = sum(
        item.get("audited_official_fact_count", 0) for item in unsupported_checks
    )
    unsupported_count = sum(
        item.get("unsupported_count", 0) for item in unsupported_checks
    )
    unsupported_unknown = sum(
        item.get("not_evaluable_count", 0) for item in unsupported_checks
    )
    unsupported_check_unknown = any(
        item.get("status") == "not_evaluable" for item in unsupported_checks
    )
    unsupported_value = (
        unsupported_count / audited_claim_count
        if audited_claim_count and not unsupported_unknown
        else None
    )
    unsupported_claim_rate = {
        "status": (
            "failed"
            if unsupported_count > thresholds["unsupported_claim_rate_max"]
            or any(item.get("status") == "failed" for item in unsupported_checks)
            else "not_evaluable"
            if unsupported_check_unknown or unsupported_value is None
            else "passed"
        ),
        "observed": unsupported_value,
        "threshold": thresholds["unsupported_claim_rate_max"],
        "audited_claim_count": audited_claim_count,
        "unsupported_count": unsupported_count,
        "not_evaluable_count": unsupported_unknown,
    }

    personal_checks = [
        item.get("personal_eligibility_conclusions", {}) for item in checks
    ]
    personal_count = sum(item.get("count", 0) for item in personal_checks)
    personal_unknown = any(
        item.get("status") == "not_evaluable" for item in personal_checks
    )
    personal_eligibility = {
        "status": (
            "failed"
            if personal_count > thresholds["personal_eligibility_conclusions_max"]
            else "not_evaluable"
            if personal_unknown
            else "passed"
        ),
        "observed": personal_count,
        "threshold": thresholds["personal_eligibility_conclusions_max"],
    }

    case_count = len(case_results)
    behavior_checks = [item.get("behavior", {}) for item in checks]
    behavior_applicable = [
        item
        for item in behavior_checks
        if item.get("status") != "not_applicable"
    ]
    behavior_case_count = len(behavior_applicable)
    behavior_passed = sum(
        item.get("status") == "passed" for item in behavior_applicable
    )
    behavior_failed = sum(
        item.get("status") == "failed" for item in behavior_applicable
    )
    behavior_unknown = sum(
        item.get("status") == "not_evaluable" for item in behavior_applicable
    )
    behavior_value = (
        behavior_passed / behavior_case_count if behavior_case_count else None
    )
    behavior_best_possible = (
        (behavior_passed + behavior_unknown) / behavior_case_count
        if behavior_case_count
        else None
    )
    behavior_accuracy = {
        "status": (
            "not_evaluable"
            if behavior_value is None
            else "failed"
            if behavior_best_possible is not None
            and behavior_best_possible
            < thresholds["clarify_answer_refuse_accuracy_min"]
            else "not_evaluable"
            if behavior_unknown
            else "passed"
            if behavior_value >= thresholds["clarify_answer_refuse_accuracy_min"]
            else "failed"
        ),
        "observed": behavior_value,
        "threshold": thresholds["clarify_answer_refuse_accuracy_min"],
        "passed_case_count": behavior_passed,
        "failed_case_count": behavior_failed,
        "not_evaluable_case_count": behavior_unknown,
        "best_possible": behavior_best_possible,
        "applicable_case_count": behavior_case_count,
        "not_applicable_case_count": case_count - behavior_case_count,
        "case_count": behavior_case_count,
    }

    trust_passed = sum(
        item.get("trust_indicator_correctness", {}).get("status") == "passed"
        for item in checks
    )
    trust_failed = sum(
        item.get("trust_indicator_correctness", {}).get("status") == "failed"
        for item in checks
    )
    trust_unknown = sum(
        item.get("trust_indicator_correctness", {}).get("status") == "not_evaluable"
        for item in checks
    )
    trust_value = trust_passed / case_count if case_count else None
    trust_best_possible = (
        (trust_passed + trust_unknown) / case_count if case_count else None
    )
    trust_indicator_correctness = {
        "status": (
            "not_evaluable"
            if trust_value is None
            else "failed"
            if trust_best_possible is not None
            and trust_best_possible < thresholds["trust_indicator_correctness_min"]
            else "not_evaluable"
            if trust_unknown
            else "passed"
            if trust_value >= thresholds["trust_indicator_correctness_min"]
            else "failed"
        ),
        "observed": trust_value,
        "threshold": thresholds["trust_indicator_correctness_min"],
        "passed_case_count": trust_passed,
        "failed_case_count": trust_failed,
        "not_evaluable_case_count": trust_unknown,
        "best_possible": trust_best_possible,
        "case_count": case_count,
    }

    freshness_checks = [
        item.get("fresh_tomato_min_material_source_rule", {}) for item in checks
    ]
    freshness_applicable = [
        item for item in freshness_checks if item.get("status") != "not_applicable"
    ]
    freshness_passed = sum(
        item.get("status") == "passed" for item in freshness_applicable
    )
    freshness_failed = sum(
        item.get("status") == "failed" for item in freshness_applicable
    )
    freshness_unknown = sum(
        item.get("status") == "not_evaluable" for item in freshness_applicable
    )
    freshness_case_count = len(freshness_applicable)
    freshness_value = (
        freshness_passed / freshness_case_count if freshness_case_count else None
    )
    freshness_rule = {
        "status": (
            "not_evaluable"
            if freshness_value is None
            else "failed"
            if freshness_failed
            else "not_evaluable"
            if freshness_unknown
            else "passed"
            if freshness_value
            >= thresholds["fresh_tomato_min_material_source_rule_pass_rate_min"]
            else "failed"
        ),
        "observed": freshness_value,
        "threshold": thresholds[
            "fresh_tomato_min_material_source_rule_pass_rate_min"
        ],
        "passed_case_count": freshness_passed,
        "failed_case_count": freshness_failed,
        "not_evaluable_case_count": freshness_unknown,
        "applicable_case_count": freshness_case_count,
    }

    required_domain_checks = [item.get("required_source_domains", {}) for item in checks]
    required_domain_total = sum(
        len(item.get("required", [])) for item in required_domain_checks
    )
    missing_domain_count = sum(
        len(item.get("missing", [])) for item in required_domain_checks
    )
    required_domain_unknown = any(
        item.get("status") == "not_evaluable" for item in required_domain_checks
    )
    required_domain_value = (
        (required_domain_total - missing_domain_count) / required_domain_total
        if required_domain_total
        else 1.0
    )
    required_domain_coverage = {
        "status": (
            "failed"
            if required_domain_value < 1.0
            else "not_evaluable"
            if required_domain_unknown
            else "passed"
        ),
        "observed": required_domain_value,
        "threshold": 1.0,
        "required_domain_count": required_domain_total,
        "missing_domain_count": missing_domain_count,
    }

    forbidden_domain_checks = [
        item.get("forbidden_source_domains", {}) for item in checks
    ]
    forbidden_domain_violations = sum(
        len(item.get("violations", [])) for item in forbidden_domain_checks
    )
    forbidden_domain_unknown = any(
        item.get("status") == "not_evaluable" for item in forbidden_domain_checks
    )
    forbidden_domain_metric = {
        "status": (
            "failed"
            if forbidden_domain_violations
            else "not_evaluable"
            if forbidden_domain_unknown
            else "passed"
        ),
        "observed": forbidden_domain_violations,
        "threshold": 0,
    }

    privacy_checks = [item.get("privacy_requirements", {}) for item in checks]
    privacy_total = sum(item.get("expectation_count", 0) for item in privacy_checks)
    privacy_passed = sum(item.get("compliant_count", 0) for item in privacy_checks)
    privacy_failed = sum(item.get("failed_count", 0) for item in privacy_checks)
    privacy_unknown = sum(
        item.get("not_evaluable_count", 0) for item in privacy_checks
    )
    privacy_value = (
        privacy_passed / privacy_total if privacy_total and not privacy_unknown else None
    )
    privacy_requirement_compliance = {
        "status": (
            "failed"
            if privacy_failed
            else "not_evaluable"
            if privacy_unknown or privacy_value is None
            else "passed"
            if privacy_value == 1.0
            else "failed"
        ),
        "observed": privacy_value,
        "threshold": 1.0,
        "expectation_count": privacy_total,
        "compliant_count": privacy_passed,
        "failed_count": privacy_failed,
        "not_evaluable_count": privacy_unknown,
    }

    execution_errors = sum(bool(result.get("error_type")) for result in case_results)
    case_execution_errors = {
        "status": "passed" if execution_errors == 0 else "failed",
        "observed": execution_errors,
        "threshold": 0,
    }

    completed_case_count = sum(
        bool(result.get("evaluation_completed")) for result in case_results
    )
    surface_completion_value = (
        completed_case_count / len(case_results) if case_results else None
    )
    evaluation_surface_completion = {
        "status": (
            "not_evaluable"
            if surface_completion_value is None or surface_completion_value < 1.0
            else "passed"
        ),
        "observed": surface_completion_value,
        "threshold": 1.0,
        "completed_case_count": completed_case_count,
        "case_count": len(case_results),
    }

    return {
        "required_fact_coverage": required_fact_coverage,
        "forbidden_claims": forbidden_claims,
        "privacy_requirement_compliance": privacy_requirement_compliance,
        "official_fact_citation_coverage": official_fact_citation_coverage,
        "citation_correctness": citation_correctness,
        "unsupported_claim_rate": unsupported_claim_rate,
        "personal_eligibility_conclusions": personal_eligibility,
        "clarify_answer_refuse_accuracy": behavior_accuracy,
        "trust_indicator_correctness": trust_indicator_correctness,
        "fresh_tomato_min_material_source_rule_pass_rate": freshness_rule,
        "required_source_domain_coverage": required_domain_coverage,
        "forbidden_source_domain_violations": forbidden_domain_metric,
        "case_execution_errors": case_execution_errors,
        "evaluation_surface_completion": evaluation_surface_completion,
    }


def _observed_behavior(
    payload: dict[str, Any],
    official_facts: list[dict[str, Any]],
    refusals: list[dict[str, Any]],
) -> str:
    if payload.get("response_kind") == "clarification" or any(
        item.get("kind") == "clarification" for item in payload.get("sections", [])
    ):
        return "clarify"
    if official_facts and refusals:
        return "answer-with-refusal"
    if refusals:
        return "refuse"
    return "answer"


def _content_tokens(text: str) -> list[str]:
    return re.findall(r"[0-9a-zA-ZæøåÆØÅ]+", text.casefold())


def _document_search_text(document: dict[str, Any]) -> str:
    return " ".join(
        [
            str(document.get("title", "")),
            str(document.get("content", "")),
            str(document.get("english_search_terms", "")),
            " ".join(str(item) for item in document.get("topic_tags", [])),
        ]
    )


def _eligible_material_source(evidence: dict[str, Any]) -> bool:
    return (
        evidence.get("review_state")
        in {"approved-current", "overdue-policy-usable"}
        and evidence.get("source_health") in {"healthy", "overdue-policy-usable"}
        and evidence.get("approval_state", "approved") == "approved"
    )


def _exact_claim_support(claim: str, evidence: dict[str, Any]) -> bool:
    normalized_claim = " ".join(claim.casefold().split())
    normalized_evidence = " ".join(str(evidence.get("content", "")).casefold().split())
    return bool(normalized_claim) and normalized_claim in normalized_evidence


def _personal_eligibility_conclusion_count(
    sections: list[dict[str, Any]],
) -> int:
    conclusion_phrases = (
        "you qualify for permanent residence",
        "you do not qualify for permanent residence",
        "you are eligible for permanent residence",
        "you are not eligible for permanent residence",
        "your application qualifies",
        "your application will be approved",
        "your application will be rejected",
    )
    count = 0
    for section in sections:
        if section.get("kind") in {"refusal", "source_warning", "clarification"}:
            continue
        lookup = " ".join(str(section.get("text", "")).casefold().split())
        count += sum(phrase in lookup for phrase in conclusion_phrases)
    return count


def _expected_evidence_confidence(
    *,
    official_fact_count: int,
    cited_official_fact_count: int,
    material_evidence: list[dict[str, Any]],
) -> str:
    if official_fact_count == 0:
        return "Low"
    conflicting = any(
        item.get("conflicts_with_answer") is True
        or str(item.get("agreement_state", "supports")).casefold()
        in {"conflict", "conflicts", "conflicting", "contradicts"}
        for item in material_evidence
    )
    if cited_official_fact_count == official_fact_count and not conflicting:
        return "High"
    return "Low"


def _expected_fresh_tomato(material_evidence: list[dict[str, Any]]) -> str:
    if not material_evidence:
        return "Low"
    scores = []
    for item in material_evidence:
        health = str(item.get("source_health", "unknown"))
        if health == "healthy":
            scores.append("High")
        elif health == "overdue-policy-usable":
            scores.append("Medium")
        else:
            scores.append("Low")
    order = {"Low": 0, "Medium": 1, "High": 2}
    return min(scores, key=order.__getitem__)


def _explicit_trust_value(indicators: list[Any], label: str) -> str | None:
    prefix = f"{label}:"
    for indicator in indicators:
        text = str(indicator).strip()
        if text.startswith(prefix):
            return text.split(":", 1)[1].strip()
    return None


def _source_domain(url: str) -> str:
    return _normalize_domain(urlparse(url).hostname or "")


def _normalize_domain(domain: Any) -> str:
    normalized = str(domain).strip().casefold().rstrip(".")
    if normalized.startswith("www."):
        normalized = normalized[4:]
    return normalized


def fingerprint_case_execution(execution: CaseExecution) -> str:
    """Bind an adjudication to the exact answer and evidence it reviewed."""

    return _sha256_json(_case_execution_private_payload(execution))


def fingerprint_answer_review_payload(
    case: dict[str, Any], execution: CaseExecution
) -> str:
    """Bind review to the exact prompt, assertion contract, answer, and evidence."""

    return _sha256_json(_answer_review_payload(case, execution))


def _answer_review_payload(
    case: dict[str, Any], execution: CaseExecution
) -> dict[str, Any]:
    return {
        "case_id": str(case["id"]),
        "evaluation_surface": "answer-path",
        "prompt": str(case["prompt"]),
        "assertions": evaluation_case_assertion_specs(case),
        "execution_sha256": fingerprint_case_execution(execution),
        "execution": _case_execution_private_payload(execution),
    }


def _case_execution_private_payload(execution: CaseExecution) -> dict[str, Any]:
    """Serialize the sensitive execution exactly once for hashing and local review."""

    if execution.result is None:
        result_payload: dict[str, Any] | None = None
    else:
        result_payload = {
            "question": execution.result.question,
            "normalized_question": execution.result.normalized_question,
            "answer": execution.result.answer,
            "model_identity": execution.result.model_identity,
            "corpus_identity": execution.result.corpus_identity,
        }
    return {
        "case_id": execution.case_id,
        "result": result_payload,
        "evidence": execution.evidence,
        "error_type": execution.error_type,
    }


def _validate_answer_adjudication(
    case: dict[str, Any],
    *,
    execution: CaseExecution,
    adjudication: dict[str, Any],
) -> None:
    case_id = str(case["id"])
    _validate_case_adjudication_common(case, adjudication)
    binding = adjudication.get("evidence_binding")
    if not isinstance(binding, dict) or binding.get("kind") != "answer-review-payload":
        raise FinalAnswerEvaluationError(
            f"case {case_id} answer adjudication requires an answer-review-payload binding"
        )
    execution_sha256 = fingerprint_case_execution(execution)
    if binding.get("execution_sha256") != execution_sha256:
        raise FinalAnswerEvaluationError(
            f"case {case_id} adjudication does not match the exact answer execution"
        )
    if binding.get("sha256") != fingerprint_answer_review_payload(case, execution):
        raise FinalAnswerEvaluationError(
            f"case {case_id} adjudication does not match the exact answer review payload"
        )
    if adjudication.get("assessment_method") != "independent-human-review":
        raise FinalAnswerEvaluationError(
            f"case {case_id} semantic adjudication requires independent-human-review"
        )


def _validate_case_adjudication_common(
    case: dict[str, Any], adjudication: dict[str, Any]
) -> None:
    case_id = str(case["id"])
    if adjudication.get("schema_version") != CASE_ADJUDICATION_SCHEMA_VERSION:
        raise FinalAnswerEvaluationError(
            f"case {case_id} adjudication has an unsupported schema version"
        )
    if adjudication.get("case_id") != case_id:
        raise FinalAnswerEvaluationError(
            f"case {case_id} adjudication is bound to a different case"
        )
    if adjudication.get("evaluation_surface") != case.get(
        "evaluation_surface", "answer-path"
    ):
        raise FinalAnswerEvaluationError(
            f"case {case_id} adjudication has the wrong evaluation surface"
        )
    results = adjudication.get("assertion_results")
    if not isinstance(results, dict):
        raise FinalAnswerEvaluationError(
            f"case {case_id} adjudication assertion_results must be an object"
        )
    known_ids = {
        item["assertion_id"] for item in evaluation_case_assertion_specs(case)
    }
    unknown_ids = sorted(set(results) - known_ids)
    if unknown_ids:
        raise FinalAnswerEvaluationError(
            f"case {case_id} adjudication contains unknown assertion IDs: "
            f"{', '.join(unknown_ids)}"
        )
    invalid_status_ids = sorted(
        assertion_id
        for assertion_id, status in results.items()
        if status not in {"passed", "failed", "not_evaluable"}
    )
    if invalid_status_ids:
        raise FinalAnswerEvaluationError(
            f"case {case_id} adjudication contains invalid assertion statuses: "
            f"{', '.join(invalid_status_ids)}"
        )


def _assertion_group_check(
    case: dict[str, Any],
    adjudication: dict[str, Any] | None,
    *,
    group: str,
    success_count_key: str,
) -> dict[str, Any]:
    assertion_ids = [
        item["assertion_id"]
        for item in evaluation_case_assertion_specs(case)
        if item["expectation_group"] == group
    ]
    results = (
        adjudication.get("assertion_results", {})
        if isinstance(adjudication, dict)
        else {}
    )
    observed = {
        assertion_id: results.get(assertion_id, "not_evaluable")
        for assertion_id in assertion_ids
    }
    passed_count = sum(status == "passed" for status in observed.values())
    failed_count = sum(status == "failed" for status in observed.values())
    unknown_ids = sorted(
        assertion_id
        for assertion_id, status in observed.items()
        if status == "not_evaluable"
    )
    if failed_count:
        status = "failed"
    elif unknown_ids:
        status = "not_evaluable"
    else:
        status = "passed"
    check: dict[str, Any] = {
        "status": status,
        "expectation_count": len(assertion_ids),
        success_count_key: passed_count,
        "failed_count": failed_count,
        "adjudicated_count": len(assertion_ids) - len(unknown_ids),
        "not_evaluable_count": len(unknown_ids),
        "assertion_ids": assertion_ids,
    }
    if unknown_ids:
        check["missing_or_not_evaluable_assertion_ids"] = unknown_ids
        check["reason"] = (
            "Approved prose expectations require an evidence-bound independent "
            "adjudication; no semantic pass was inferred."
        )
    if assertion_ids and not unknown_ids:
        check["value"] = passed_count / len(assertion_ids)
    elif not assertion_ids:
        check["value"] = 1.0
    else:
        check["value"] = None
    return check


if __name__ == "__main__":
    raise SystemExit(main())
