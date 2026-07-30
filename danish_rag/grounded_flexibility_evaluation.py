"""Evaluation contract for paraphrased and composite grounded questions."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from .final_answer_evaluation import evaluate_final_answer_case
from .source_freshness import assess_source_freshness


SCHEMA_VERSION = "grounded-flexibility-evaluation-v1"


class GroundedFlexibilityEvaluationError(ValueError):
    """Raised when the grounded-flexibility dataset is not trustworthy."""


def load_grounded_flexibility_dataset(path: str | Path) -> dict[str, Any]:
    """Load and validate the reviewed synthetic grounded-flexibility cases."""

    dataset_path = Path(path)
    try:
        dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GroundedFlexibilityEvaluationError(
            f"Could not load grounded-flexibility dataset {dataset_path}: {exc}"
        ) from exc
    if not isinstance(dataset, dict):
        raise GroundedFlexibilityEvaluationError(
            "Grounded-flexibility dataset must be a JSON object."
        )

    failures = _validate_dataset(dataset)
    if failures:
        raise GroundedFlexibilityEvaluationError("; ".join(failures))
    return dataset


def generate_grounded_flexibility_evaluation(
    dataset_path: str | Path,
    *,
    runner: Any,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Execute the synthetic cases and publish content-free coverage evidence."""

    dataset_path = Path(dataset_path)
    dataset = load_grounded_flexibility_dataset(dataset_path)
    evaluated_at_utc = generated_at_utc or _utc_now()
    facts = {fact["fact_id"]: fact for fact in dataset["facts"]}
    unsupported_parts = {
        part["part_id"]: part for part in dataset["unsupported_parts"]
    }
    case_results = []
    for case in dataset["cases"]:
        runner_case = {**case, "id": case["case_id"]}
        case_results.append(
            _evaluate_case(
                case,
                runner.run(runner_case),
                facts=facts,
                unsupported_parts=unsupported_parts,
                evaluated_at_utc=evaluated_at_utc,
            )
        )
    thresholds = dataset["thresholds"]
    per_intent = _aggregate_intent_evidence(
        case_results,
        threshold=thresholds["per_intent_evidence_coverage_min"],
    )
    metrics = {
        "per_intent_evidence_coverage": per_intent,
        "required_fact_coverage": _aggregate_required_fact_coverage(
            case_results,
            threshold=thresholds["required_fact_coverage_min"],
        ),
        "citation_correctness": _aggregate_citation_correctness(
            case_results,
            threshold=thresholds["citation_correctness_min"],
        ),
        "unsupported_claim_rate": _aggregate_unsupported_claims(
            case_results,
            threshold=thresholds["unsupported_claim_rate_max"],
        ),
        "refusal_precision": _aggregate_refusal_precision(
            case_results,
            threshold=thresholds["refusal_precision_min"],
        ),
        "paraphrase_consistency": _aggregate_paraphrase_consistency(
            case_results,
            threshold=thresholds["paraphrase_consistency_min"],
        ),
    }
    threshold_failures = [
        metric_id
        for metric_id, metric in metrics.items()
        if metric["status"] != "passed"
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": evaluated_at_utc,
        "dataset": {
            "dataset_id": dataset["dataset_id"],
            "version": dataset["version"],
            "case_count": len(dataset["cases"]),
            "sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        },
        "identity": dict(runner.public_identity),
        "case_results": case_results,
        "metrics": metrics,
        "threshold_failures": threshold_failures,
        "strict_passed": not threshold_failures,
        "privacy_assertions": {
            "contains_case_prompts": False,
            "contains_generated_answer_text": False,
            "contains_conversation_records": False,
            "uses_production_user_content": False,
        },
    }


def write_grounded_flexibility_evaluation_report(
    report: dict[str, Any], output_path: str | Path
) -> None:
    """Persist content-free qualification evidence for release validation."""

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _evaluate_case(
    case: dict[str, Any],
    execution: Any,
    *,
    facts: dict[str, dict[str, Any]],
    unsupported_parts: dict[str, dict[str, Any]],
    evaluated_at_utc: str,
) -> dict[str, Any]:
    eligible_document_ids = {
        str(item.get("document_id") or item.get("citation_id"))
        for item in execution.evidence
        if assess_source_freshness(
            item, evaluated_at_utc=evaluated_at_utc
        ).answer_eligible
        and (item.get("document_id") or item.get("citation_id"))
    }
    intent_evidence = []
    for expected_intent in case["expected_intents"]:
        required = list(expected_intent["required_document_ids"])
        observed = sorted(set(required).intersection(eligible_document_ids))
        missing = sorted(set(required) - set(observed))
        intent_evidence.append(
            {
                "intent_id": expected_intent["intent_id"],
                "required_document_ids": required,
                "observed_document_ids": observed,
                "missing_document_ids": missing,
                "status": "passed" if not missing else "failed",
            }
        )

    expected_fact_ids = sorted(
        {
            fact_id
            for intent in case["expected_intents"]
            for fact_id in intent["required_fact_ids"]
        }
    )
    required_document_ids = {
        document_id
        for intent in case["expected_intents"]
        for document_id in intent["required_document_ids"]
    }
    answer = execution.result.answer if execution.result is not None else {}
    official_facts = [
        section
        for section in answer.get("sections", [])
        if isinstance(section, dict) and section.get("kind") == "official_fact"
    ]
    material_citation_ids = sorted(
        {
            str(citation_id)
            for section in official_facts
            for citation_id in section.get("citation_ids", [])
            if str(citation_id) in required_document_ids
        }
    )
    supported_fact_ids = sorted(
        fact_id
        for fact_id in expected_fact_ids
        if _answer_states_supported_fact(facts[fact_id], official_facts)
    )
    missing_fact_ids = sorted(set(expected_fact_ids) - set(supported_fact_ids))
    refusals = [
        section
        for section in answer.get("sections", [])
        if isinstance(section, dict) and section.get("kind") == "refusal"
    ]
    observed_unsupported_part_ids = sorted(
        part_id
        for part_id, part in unsupported_parts.items()
        if any(_refusal_matches_part(refusal, part) for refusal in refusals)
    )
    matched_refusal_indexes = {
        index
        for index, refusal in enumerate(refusals)
        if any(
            _refusal_matches_part(refusal, part)
            for part in unsupported_parts.values()
        )
    }
    expected_unsupported_part_ids = set(case["unsupported_part_ids"])
    missing_unsupported_part_ids = sorted(
        expected_unsupported_part_ids - set(observed_unsupported_part_ids)
    )
    unexpected_unsupported_part_ids = sorted(
        set(observed_unsupported_part_ids) - expected_unsupported_part_ids
    )
    unmatched_refusal_count = len(refusals) - len(matched_refusal_indexes)
    structural = _structural_answer_checks(case, execution)
    checks = structural.get("checks", {})
    required_checks = (
        "behavior",
        "official_fact_citation_coverage",
        "citation_correctness",
        "unsupported_claims",
    )
    failed = (
        bool(execution.error_type)
        or any(item["status"] == "failed" for item in intent_evidence)
        or bool(missing_fact_ids)
        or bool(missing_unsupported_part_ids)
        or bool(unexpected_unsupported_part_ids)
        or bool(unmatched_refusal_count)
        or any(checks.get(check_id, {}).get("status") != "passed" for check_id in required_checks)
    )
    return {
        "case_id": case["case_id"],
        "paraphrase_group_id": case["paraphrase_group_id"],
        "expected_behavior": case["expected_behavior"],
        "status": "failed" if failed else "passed",
        "error_type": execution.error_type or None,
        "intent_evidence": intent_evidence,
        "required_fact_ids": expected_fact_ids,
        "supported_fact_ids": supported_fact_ids,
        "missing_fact_ids": missing_fact_ids,
        "material_citation_ids": material_citation_ids,
        "unsupported_part_ids": list(case["unsupported_part_ids"]),
        "observed_unsupported_part_ids": observed_unsupported_part_ids,
        "missing_unsupported_part_ids": missing_unsupported_part_ids,
        "unexpected_unsupported_part_ids": unexpected_unsupported_part_ids,
        "unmatched_refusal_count": unmatched_refusal_count,
        "checks": {
            check_id: checks.get(check_id, {"status": "not_evaluable"})
            for check_id in required_checks
        },
    }


def _structural_answer_checks(
    case: dict[str, Any], execution: Any
) -> dict[str, Any]:
    if execution.result is None:
        return {"checks": {}}
    evaluation_case = {
        "id": case["case_id"],
        "final_answer_expectations": {
            "expected_behavior": case["expected_behavior"],
            "required_facts": [],
            "forbidden_claims": [],
            "required_citation_domains": [],
            "forbidden_source_domains": [],
            "trust_indicators": [],
            "privacy_requirements": [],
        },
    }
    return evaluate_final_answer_case(evaluation_case, execution)


def _answer_states_supported_fact(
    fact: dict[str, Any], official_facts: list[dict[str, Any]]
) -> bool:
    supporting_document_ids = set(fact["supported_by_document_ids"])
    claim_markers = [
        " ".join(str(marker).casefold().split())
        for marker in fact["claim_markers"]
    ]
    for section in official_facts:
        citation_ids = {str(item) for item in section.get("citation_ids", [])}
        if not supporting_document_ids.intersection(citation_ids):
            continue
        normalized_text = " ".join(str(section.get("text", "")).casefold().split())
        if all(marker in normalized_text for marker in claim_markers):
            return True
    return False


def _refusal_matches_part(
    refusal: dict[str, Any], part: dict[str, Any]
) -> bool:
    normalized_text = " ".join(str(refusal.get("text", "")).casefold().split())
    return all(
        any(
            " ".join(str(term).casefold().split()) in normalized_text
            for term in term_group
        )
        for term_group in part["required_refusal_term_groups"]
    )


def _aggregate_intent_evidence(
    case_results: list[dict[str, Any]], *, threshold: float
) -> dict[str, Any]:
    totals: dict[str, dict[str, int]] = {}
    for case in case_results:
        for intent in case["intent_evidence"]:
            counts = totals.setdefault(
                intent["intent_id"], {"required": 0, "covered": 0}
            )
            counts["required"] += len(intent["required_document_ids"])
            counts["covered"] += len(intent["observed_document_ids"])

    by_intent = {}
    for intent_id, counts in sorted(totals.items()):
        observed = counts["covered"] / counts["required"]
        by_intent[intent_id] = {
            "status": "passed" if observed >= threshold else "failed",
            "observed": observed,
            "threshold": threshold,
            "covered_document_count": counts["covered"],
            "required_document_count": counts["required"],
        }
    return {
        "status": (
            "passed"
            if by_intent and all(item["status"] == "passed" for item in by_intent.values())
            else "failed"
        ),
        "threshold": threshold,
        "by_intent": by_intent,
    }


def _aggregate_required_fact_coverage(
    case_results: list[dict[str, Any]], *, threshold: float
) -> dict[str, Any]:
    required_count = sum(len(case["required_fact_ids"]) for case in case_results)
    covered_count = sum(len(case["supported_fact_ids"]) for case in case_results)
    observed = covered_count / required_count if required_count else None
    return {
        "status": (
            "passed"
            if observed is not None and observed >= threshold
            else "failed"
        ),
        "observed": observed,
        "threshold": threshold,
        "covered_fact_count": covered_count,
        "required_fact_count": required_count,
    }


def _aggregate_citation_correctness(
    case_results: list[dict[str, Any]], *, threshold: float
) -> dict[str, Any]:
    checks = [case["checks"]["citation_correctness"] for case in case_results]
    relation_count = sum(check.get("relation_count", 0) for check in checks)
    supported_count = sum(
        check.get("supported_relation_count", 0) for check in checks
    )
    incorrect_count = sum(
        check.get("incorrect_relation_count", 0) for check in checks
    )
    not_evaluable_count = sum(
        check.get("not_evaluable_relation_count", 0) for check in checks
    )
    observed = (
        supported_count / relation_count
        if relation_count and not not_evaluable_count
        else None
    )
    check_statuses = {check.get("status") for check in checks}
    if incorrect_count or "failed" in check_statuses:
        status = "failed"
    elif check_statuses != {"passed"} or observed is None:
        status = "not_evaluable"
    else:
        status = "passed" if observed >= threshold else "failed"
    return {
        "status": status,
        "observed": observed,
        "threshold": threshold,
        "relation_count": relation_count,
        "supported_relation_count": supported_count,
        "incorrect_relation_count": incorrect_count,
        "not_evaluable_relation_count": not_evaluable_count,
    }


def _aggregate_unsupported_claims(
    case_results: list[dict[str, Any]], *, threshold: float
) -> dict[str, Any]:
    checks = [case["checks"]["unsupported_claims"] for case in case_results]
    audited_count = sum(
        check.get("audited_official_fact_count", 0) for check in checks
    )
    unsupported_count = sum(check.get("unsupported_count", 0) for check in checks)
    not_evaluable_count = sum(
        check.get("not_evaluable_count", 0) for check in checks
    )
    observed = (
        unsupported_count / audited_count
        if audited_count and not not_evaluable_count
        else None
    )
    check_statuses = {check.get("status") for check in checks}
    if unsupported_count > 0 or "failed" in check_statuses:
        status = "failed"
    elif check_statuses != {"passed"} or observed is None:
        status = "not_evaluable"
    else:
        status = "passed" if observed <= threshold else "failed"
    return {
        "status": status,
        "observed": observed,
        "threshold": threshold,
        "audited_claim_count": audited_count,
        "unsupported_claim_count": unsupported_count,
        "not_evaluable_claim_count": not_evaluable_count,
    }


def _aggregate_refusal_precision(
    case_results: list[dict[str, Any]], *, threshold: float
) -> dict[str, Any]:
    expected_count = 0
    observed_count = 0
    correct_count = 0
    false_positive_count = 0
    missing_count = 0
    for case in case_results:
        expected_parts = set(case["unsupported_part_ids"])
        observed_parts = set(case["observed_unsupported_part_ids"])
        correct = expected_parts.intersection(observed_parts)
        false_positives = observed_parts - expected_parts
        missing = expected_parts - observed_parts
        unmatched = case["unmatched_refusal_count"]
        expected_count += len(expected_parts)
        correct_count += len(correct)
        false_positive_count += len(false_positives) + unmatched
        missing_count += len(missing)
        observed_count += len(correct) + len(false_positives) + unmatched
    observed_precision = (
        correct_count / observed_count if observed_count else None
    )
    return {
        "status": (
            "passed"
            if observed_precision is not None
            and observed_precision >= threshold
            and missing_count == 0
            else "failed"
        ),
        "observed": observed_precision,
        "threshold": threshold,
        "expected_refusal_count": expected_count,
        "observed_refusal_count": observed_count,
        "correct_scoped_refusal_count": correct_count,
        "false_positive_refusal_count": false_positive_count,
        "missing_required_refusal_count": missing_count,
    }


def _aggregate_paraphrase_consistency(
    case_results: list[dict[str, Any]], *, threshold: float
) -> dict[str, Any]:
    cases_by_group: dict[str, list[dict[str, Any]]] = {}
    for case in case_results:
        cases_by_group.setdefault(case["paraphrase_group_id"], []).append(case)

    group_results = []
    for group_id, cases in sorted(cases_by_group.items()):
        if len(cases) < 2:
            continue
        fact_sets = {tuple(case["supported_fact_ids"]) for case in cases}
        citation_sets = {tuple(case["material_citation_ids"]) for case in cases}
        missing_required_facts = sum(
            len(case["missing_fact_ids"]) for case in cases
        )
        consistent = (
            len(fact_sets) == 1
            and len(citation_sets) == 1
            and missing_required_facts == 0
        )
        group_results.append(
            {
                "paraphrase_group_id": group_id,
                "status": "passed" if consistent else "failed",
                "case_count": len(cases),
                "missing_required_fact_count": missing_required_facts,
            }
        )
    passed_count = sum(group["status"] == "passed" for group in group_results)
    observed = passed_count / len(group_results) if group_results else None
    return {
        "status": (
            "passed"
            if observed is not None and observed >= threshold
            else "failed"
        ),
        "observed": observed,
        "threshold": threshold,
        "group_count": len(group_results),
        "passed_group_count": passed_count,
        "comparison_basis": ["supported_fact_ids", "material_citation_ids"],
        "requires_identical_answer_prose": False,
        "groups": group_results,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _validate_dataset(dataset: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if dataset.get("schema_version") != SCHEMA_VERSION:
        failures.append(f"schema_version must be {SCHEMA_VERSION!r}")
    if dataset.get("review_issue") != 48:
        failures.append("review_issue must identify issue 48")

    privacy = dataset.get("privacy")
    privacy_fields = (
        "uses_production_user_questions",
        "uses_production_user_conversations",
        "uses_production_user_answers",
    )
    if not isinstance(privacy, dict) or any(
        privacy.get(field) is not False for field in privacy_fields
    ):
        failures.append("privacy contract must exclude all production-user content")

    overlap_contract = dataset.get("lexical_overlap")
    overlap_max = (
        overlap_contract.get("maximum_pairwise_jaccard")
        if isinstance(overlap_contract, dict)
        else None
    )
    if not isinstance(overlap_max, int | float) or not 0 <= overlap_max < 1:
        failures.append("lexical overlap maximum must be a number from 0 through 1")
        overlap_max = 0.0

    thresholds = dataset.get("thresholds")
    threshold_ids = (
        "per_intent_evidence_coverage_min",
        "required_fact_coverage_min",
        "citation_correctness_min",
        "unsupported_claim_rate_max",
        "refusal_precision_min",
        "paraphrase_consistency_min",
    )
    if not isinstance(thresholds, dict):
        failures.append("thresholds must be an object")
    else:
        for threshold_id in threshold_ids:
            value = thresholds.get(threshold_id)
            if not isinstance(value, int | float) or not 0 <= value <= 1:
                failures.append(
                    f"threshold {threshold_id!r} must be a number from 0 through 1"
                )

    facts = dataset.get("facts")
    fact_ids: set[str] = set()
    if not isinstance(facts, list) or not facts:
        failures.append("facts must be a non-empty list")
        facts = []
    for fact in facts:
        if not isinstance(fact, dict):
            failures.append("each fact must be an object")
            continue
        fact_id = _non_empty_text(fact.get("fact_id"))
        supporting_documents = _non_empty_text_list(
            fact.get("supported_by_document_ids")
        )
        claim_markers = _non_empty_text_list(fact.get("claim_markers"))
        if fact_id is None:
            failures.append("each fact must have a non-empty fact_id")
        elif fact_id in fact_ids:
            failures.append(f"fact_id {fact_id!r} is duplicated")
        else:
            fact_ids.add(fact_id)
        if supporting_documents is None:
            failures.append(
                f"fact {fact_id!r} must name at least one supporting document"
            )
        if claim_markers is None:
            failures.append(
                f"fact {fact_id!r} must declare machine-checkable claim markers"
            )

    unsupported_part_definitions = dataset.get("unsupported_parts")
    unsupported_part_ids: set[str] = set()
    if not isinstance(unsupported_part_definitions, list) or not unsupported_part_definitions:
        failures.append("unsupported_parts must be a non-empty list")
        unsupported_part_definitions = []
    for part in unsupported_part_definitions:
        if not isinstance(part, dict):
            failures.append("each unsupported part must be an object")
            continue
        part_id = _non_empty_text(part.get("part_id"))
        term_groups = _non_empty_text_groups(
            part.get("required_refusal_term_groups")
        )
        if part_id is None:
            failures.append("each unsupported part must have a non-empty part_id")
        elif part_id in unsupported_part_ids:
            failures.append(f"unsupported part {part_id!r} is duplicated")
        else:
            unsupported_part_ids.add(part_id)
        if term_groups is None:
            failures.append(
                f"unsupported part {part_id!r} must declare refusal term groups"
            )

    selected_intents = dataset.get("selected_intents")
    intent_by_id: dict[str, dict[str, Any]] = {}
    if not isinstance(selected_intents, list) or not selected_intents:
        failures.append("selected_intents must be a non-empty list")
        selected_intents = []
    for intent in selected_intents:
        if not isinstance(intent, dict):
            failures.append("each selected intent must be an object")
            continue
        intent_id = _non_empty_text(intent.get("intent_id"))
        group_id = _non_empty_text(intent.get("paraphrase_group_id"))
        required_documents = _non_empty_text_list(intent.get("required_document_ids"))
        required_facts = _non_empty_text_list(intent.get("required_fact_ids"))
        if intent_id is None:
            failures.append("each selected intent must have a non-empty intent_id")
            continue
        if intent_id in intent_by_id:
            failures.append(f"intent_id {intent_id!r} is duplicated")
        intent_by_id[intent_id] = intent
        if group_id is None:
            failures.append(f"intent {intent_id!r} must name a paraphrase group")
        if required_documents is None:
            failures.append(f"intent {intent_id!r} must require evidence")
        if required_facts is None or not set(required_facts).issubset(fact_ids):
            failures.append(f"intent {intent_id!r} references unknown required facts")

    cases = dataset.get("cases")
    case_ids: set[str] = set()
    prompts: set[str] = set()
    cases_by_group: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(cases, list) or not cases:
        failures.append("cases must be a non-empty list")
        cases = []
    for case in cases:
        if not isinstance(case, dict):
            failures.append("each case must be an object")
            continue
        case_id = _non_empty_text(case.get("case_id"))
        prompt = _non_empty_text(case.get("prompt"))
        group_id = _non_empty_text(case.get("paraphrase_group_id"))
        if case_id is None:
            failures.append("each case must have a non-empty case_id")
        elif case_id in case_ids:
            failures.append(f"case_id {case_id!r} is duplicated")
        else:
            case_ids.add(case_id)
        if prompt is None:
            failures.append(f"case {case_id!r} must have a synthetic prompt")
        elif prompt.casefold() in prompts:
            failures.append(f"case {case_id!r} duplicates another prompt")
        else:
            prompts.add(prompt.casefold())
        if group_id is None:
            failures.append(f"case {case_id!r} must name a paraphrase group")
        else:
            cases_by_group.setdefault(group_id, []).append(case)

        expected_behavior = case.get("expected_behavior")
        if expected_behavior not in {"answer", "answer-with-refusal"}:
            failures.append(f"case {case_id!r} has invalid expected behavior")
        expected_intents = case.get("expected_intents")
        if not isinstance(expected_intents, list) or not expected_intents:
            failures.append(f"case {case_id!r} must declare expected intents")
            expected_intents = []
        seen_intents: set[str] = set()
        for expected_intent in expected_intents:
            if not isinstance(expected_intent, dict):
                failures.append(
                    f"case {case_id!r} expected intents must be objects"
                )
                continue
            intent_id = _non_empty_text(expected_intent.get("intent_id"))
            required_documents = _non_empty_text_list(
                expected_intent.get("required_document_ids")
            )
            required_facts = _non_empty_text_list(
                expected_intent.get("required_fact_ids")
            )
            if intent_id is None or intent_id not in intent_by_id:
                failures.append(f"case {case_id!r} references an unknown intent")
                continue
            if intent_id in seen_intents:
                failures.append(
                    f"case {case_id!r} duplicates intent {intent_id!r}"
                )
            seen_intents.add(intent_id)
            intent = intent_by_id[intent_id]
            if required_documents != intent.get("required_document_ids"):
                failures.append(
                    f"case {case_id!r} must declare all evidence for intent "
                    f"{intent_id!r}"
                )
            if required_facts != intent.get("required_fact_ids"):
                failures.append(
                    f"case {case_id!r} must declare all facts for intent "
                    f"{intent_id!r}"
                )

        if len(expected_intents) > 1:
            if case.get("requires_cross_document_synthesis") is not True:
                failures.append(
                    f"multi-intent case {case_id!r} must require cross-document synthesis"
                )
            if case.get("requires_claim_adjacent_citations") is not True:
                failures.append(
                    f"multi-intent case {case_id!r} must require adjacent citations"
                )
        unsupported_parts = case.get("unsupported_part_ids")
        if not isinstance(unsupported_parts, list) or any(
            _non_empty_text(item) is None for item in unsupported_parts
        ):
            failures.append(
                f"case {case_id!r} unsupported_part_ids must be a text list"
            )
        elif unsupported_parts and expected_behavior != "answer-with-refusal":
            failures.append(
                f"case {case_id!r} with unsupported parts must answer with refusal"
            )
        elif unsupported_parts and not set(unsupported_parts).issubset(
            unsupported_part_ids
        ):
            failures.append(f"case {case_id!r} references an unknown unsupported part")

    for intent_id, intent in intent_by_id.items():
        group_id = str(intent.get("paraphrase_group_id", ""))
        variants = cases_by_group.get(group_id, [])
        if len(variants) < 3:
            failures.append(
                f"intent {intent_id!r} must have at least three paraphrases"
            )
            continue
        for left, right in combinations(variants, 2):
            overlap = _token_jaccard(str(left["prompt"]), str(right["prompt"]))
            if overlap > overlap_max:
                failures.append(
                    f"paraphrases {left['case_id']!r} and {right['case_id']!r} "
                    f"overlap at {overlap:.3f}, above {overlap_max:.3f}"
                )

    return failures


def _token_jaccard(left: str, right: str) -> float:
    left_tokens = set(
        re.findall(r"[0-9a-z\u00e6\u00f8\u00e5]+", left.casefold())
    )
    right_tokens = set(
        re.findall(r"[0-9a-z\u00e6\u00f8\u00e5]+", right.casefold())
    )
    union = left_tokens | right_tokens
    if not union:
        return 1.0
    return len(left_tokens & right_tokens) / len(union)


def _non_empty_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _non_empty_text_list(value: Any) -> list[str] | None:
    if (
        not isinstance(value, list)
        or not value
        or any(_non_empty_text(item) is None for item in value)
    ):
        return None
    return value


def _non_empty_text_groups(value: Any) -> list[list[str]] | None:
    if not isinstance(value, list) or not value:
        return None
    groups = [_non_empty_text_list(group) for group in value]
    if any(group is None for group in groups):
        return None
    return [group for group in groups if group is not None]
