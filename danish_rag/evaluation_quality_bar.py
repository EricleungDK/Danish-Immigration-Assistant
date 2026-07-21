"""Evaluation quality-bar loading and documentation contract checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONTRACT_START = "<!-- evaluation-quality-bar-contract:start -->"
CONTRACT_END = "<!-- evaluation-quality-bar-contract:end -->"

REQUIRED_METRIC_IDS = {
    "retrieval-required-evidence-recall-at-3",
    "retrieval-blocked-source-violations",
    "official-fact-citation-coverage",
    "unsupported-claim-rate",
    "clarify-answer-refuse-behavior",
    "trust-indicator-correctness",
    "privacy-network-boundary",
    "update-rollback-success",
    "accessibility-conformance",
    "reliability-critical-journeys",
}
REQUIRED_BEHAVIOR_CLASSES = {
    "happy_path",
    "edge_case",
    "out_of_bounds",
    "ambiguity",
    "conflict",
    "stale_source",
    "refusal",
    "robustness",
}
ASSERTION_CONTRACT_SCHEMA_VERSION = "evaluation-case-assertions-v1"
ADJUDICATION_SCHEMA_VERSION = "final-answer-adjudications-v1"
ASSERTION_EXPECTATION_GROUPS = (
    "required_facts",
    "forbidden_claims",
    "trust_indicators",
    "privacy_requirements",
)
EVALUATION_SURFACES = {
    "answer-path",
    "source-policy-scenario",
    "browser-workflow",
    "knowledge-release-workflow",
    "provider-recovery-workflow",
}
APPROVAL_RECORD = (
    "Product owner approval provided through the initiating GPT goal instruction "
    "on 2026-07-13."
)


def evaluation_case_assertion_specs(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Return stable IDs for every approved prose assertion in one case.

    The prose remains the approved criterion.  The stable ID lets an independent
    adjudication or workflow artifact address that criterion without copying the
    criterion or generated answer into the public evaluation report.
    """

    case_id = str(case.get("id", ""))
    expectations = case.get("final_answer_expectations")
    if not case_id or not isinstance(expectations, dict):
        return []

    specs: list[dict[str, Any]] = []
    for group in ASSERTION_EXPECTATION_GROUPS:
        values = expectations.get(group)
        if not isinstance(values, list):
            continue
        group_id = group.replace("_", "-")
        for index, criterion in enumerate(values, start=1):
            specs.append(
                {
                    "assertion_id": f"{case_id}:{group_id}:{index:02d}",
                    "expectation_group": group,
                    "expectation_index": index,
                    "criterion": criterion,
                    "verification": (
                        "independent-semantic-adjudication"
                        if group in {"required_facts", "forbidden_claims"}
                        else "structural-check-or-evidence-bound-adjudication"
                    ),
                }
            )
    return specs


def load_evaluation_quality_bar(path: str | Path) -> dict[str, Any]:
    quality_bar = _load_json_object(path, "evaluation quality bar")
    failures = _validate_quality_bar_shape(quality_bar)
    failures.extend(validate_release_thresholds(quality_bar))
    if failures:
        joined = "; ".join(failures)
        raise ValueError(f"Invalid evaluation quality bar {path}: {joined}")
    return quality_bar


def load_evaluation_cases(path: str | Path) -> dict[str, Any]:
    cases = _load_json_object(path, "evaluation cases")
    if not isinstance(cases.get("cases"), list):
        raise ValueError(f"Invalid evaluation cases {path}: missing cases list")
    return cases


def extract_documented_quality_bar_contract(path: str | Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        contract_block = text.split(CONTRACT_START, 1)[1].split(CONTRACT_END, 1)[0]
    except IndexError as exc:
        raise ValueError("Evaluation documentation is missing quality-bar contract") from exc

    contract_text = _strip_markdown_json_fence(contract_block.strip())
    return json.loads(contract_text)


def validate_quality_bar_document_contract(
    quality_bar: dict[str, Any], documented_contract: dict[str, Any]
) -> list[str]:
    failures: list[str] = []
    expected_contract = quality_bar.get("documentation_contract")
    if documented_contract != expected_contract:
        failures.append("documented quality-bar contract differs from config")

    thresholds = quality_bar["thresholds"]
    expected_values = {
        "quality_bar_id": quality_bar["quality_bar_id"],
        "version": quality_bar["version"],
        "approval_status": quality_bar["approval_status"],
        "dataset_id": quality_bar["evaluation_set"]["dataset_id"],
        "dataset_version": quality_bar["evaluation_set"]["version"],
        "dataset_case_count": quality_bar["evaluation_set"]["case_count"],
        "runtime_baseline": quality_bar["runtime_baseline"]["baseline_id"],
        "generation_model": quality_bar["runtime_baseline"]["generation_model"],
        "embedding_model": quality_bar["retrieval_baseline"]["embedding_model"],
        "retrieval_baseline": quality_bar["retrieval_baseline"]["selected_candidate"],
        "retrieval_required_evidence_recall_at_3_min": thresholds["retrieval"][
            "required_evidence_recall_at_3_min"
        ],
        "official_fact_citation_coverage_min": thresholds["final_answer"][
            "official_fact_citation_coverage_min"
        ],
        "unsupported_claim_rate_max": thresholds["final_answer"][
            "unsupported_claim_rate_max"
        ],
        "answer_time_personal_data_egress_max": thresholds["privacy"][
            "answer_time_personal_data_egress_max"
        ],
        "accessibility_standard": thresholds["accessibility"]["standard"],
    }
    for key, expected_value in expected_values.items():
        if documented_contract.get(key) != expected_value:
            failures.append(f"documented {key!r} does not match quality bar")

    return failures


def validate_release_thresholds(quality_bar: dict[str, Any]) -> list[str]:
    thresholds = quality_bar.get("thresholds", {})
    failures: list[str] = []

    change_control = thresholds.get("change_control", {})
    if not change_control.get("weakening_requires_new_version_and_human_approval"):
        failures.append("threshold weakening must require a new version and human approval")
    if not change_control.get("release_blocking_thresholds"):
        failures.append("release-blocking thresholds must be explicit")

    retrieval = thresholds.get("retrieval", {})
    _require_minimum(
        failures,
        retrieval,
        "required_evidence_recall_at_3_min",
        0.95,
        "retrieval required evidence Recall@3",
    )
    _require_minimum(
        failures,
        retrieval,
        "critical_case_recall_at_3_min",
        1.0,
        "critical retrieval Recall@3",
    )
    _require_maximum(
        failures,
        retrieval,
        "blocked_source_violations_max",
        0,
        "blocked source violations",
    )
    _require_maximum(
        failures,
        retrieval,
        "forbidden_result_violations_max",
        0,
        "forbidden result violations",
    )

    final_answer = thresholds.get("final_answer", {})
    _require_minimum(
        failures,
        final_answer,
        "official_fact_citation_coverage_min",
        1.0,
        "official fact citation coverage",
    )
    _require_minimum(
        failures,
        final_answer,
        "citation_correctness_min",
        1.0,
        "citation correctness",
    )
    _require_maximum(
        failures,
        final_answer,
        "unsupported_claim_rate_max",
        0.0,
        "unsupported claim rate",
    )
    _require_maximum(
        failures,
        final_answer,
        "personal_eligibility_conclusions_max",
        0,
        "personal eligibility conclusions",
    )

    privacy = thresholds.get("privacy", {})
    _require_maximum(
        failures,
        privacy,
        "answer_time_personal_data_egress_max",
        0,
        "answer-time personal data egress",
    )
    if privacy.get("update_telemetry_may_include_conversation_data") is not False:
        failures.append("update telemetry must exclude conversation data")

    rollback = thresholds.get("rollback", {})
    _require_minimum(
        failures,
        rollback,
        "atomic_update_rollback_success_min",
        1.0,
        "atomic update rollback success",
    )
    _require_maximum(
        failures,
        rollback,
        "mismatched_active_corpus_index_pairs_max",
        0,
        "mismatched active corpus/index pairs",
    )

    accessibility = thresholds.get("accessibility", {})
    if accessibility.get("standard") != "WCAG 2.2 AA":
        failures.append("accessibility standard must remain WCAG 2.2 AA")
    _require_maximum(
        failures,
        accessibility,
        "critical_or_serious_automated_violations_max",
        0,
        "critical or serious accessibility violations",
    )

    reliability = thresholds.get("reliability", {})
    _require_minimum(
        failures,
        reliability,
        "critical_journey_pass_rate_min",
        1.0,
        "critical journey pass rate",
    )

    return failures


def validate_evaluation_cases(
    quality_bar: dict[str, Any], cases: dict[str, Any]
) -> list[str]:
    failures: list[str] = []
    expected_set = quality_bar["evaluation_set"]
    if cases.get("dataset_id") != expected_set["dataset_id"]:
        failures.append("evaluation dataset id does not match quality bar")
    if cases.get("version") != expected_set["version"]:
        failures.append("evaluation dataset version does not match quality bar")
    if len(cases.get("cases", [])) != expected_set["case_count"]:
        failures.append("evaluation dataset case count does not match quality bar")
    if cases.get("approval_status") != "approved":
        failures.append("evaluation dataset approval status must be approved")
    if cases.get("approval_record") != APPROVAL_RECORD:
        failures.append("evaluation dataset approval record does not match the approved decision")

    assertion_contract = cases.get("assertion_contract")
    expected_assertion_contract = {
        "schema_version": ASSERTION_CONTRACT_SCHEMA_VERSION,
        "adjudication_schema_version": ADJUDICATION_SCHEMA_VERSION,
        "assertion_id_template": (
            "{case_id}:{expectation_group_with_hyphens}:{one_based_index_2_digits}"
        ),
        "expectation_groups": list(ASSERTION_EXPECTATION_GROUPS),
        "evaluation_surfaces": sorted(EVALUATION_SURFACES),
        "semantic_rule": (
            "Required-fact coverage and forbidden-claim absence require an "
            "independent adjudication bound to the exact answer execution; prose "
            "alone never passes a machine check."
        ),
        "workflow_rule": (
            "Non-answer workflows require an evidence artifact with a verified "
            "SHA-256 and assertion results bound to that artifact."
        ),
    }
    if assertion_contract != expected_assertion_contract:
        failures.append("evaluation assertion contract is missing or differs from v1")

    behavior_classes = set()
    content_areas = set()
    case_ids = set()
    assertion_ids: set[str] = set()
    for index, case in enumerate(cases.get("cases", []), start=1):
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.startswith("eval-"):
            failures.append(f"case {index} has invalid id")
        if case_id in case_ids:
            failures.append(f"case id {case_id!r} is duplicated")
        case_ids.add(case_id)

        behavior_class = case.get("behavior_class")
        if behavior_class:
            behavior_classes.add(behavior_class)
        content_area = case.get("content_area")
        if content_area:
            content_areas.add(content_area)

        evaluation_surface = case.get("evaluation_surface")
        if evaluation_surface not in EVALUATION_SURFACES:
            failures.append(f"case {case_id!r} has invalid evaluation surface")

        retrieval = case.get("retrieval_expectations")
        final_answer = case.get("final_answer_expectations")
        if not isinstance(retrieval, dict):
            failures.append(f"case {case_id!r} is missing retrieval expectations")
            continue
        if not isinstance(final_answer, dict):
            failures.append(f"case {case_id!r} is missing final answer expectations")
            continue

        _require_list_field(failures, retrieval, "required_facts", case_id)
        _require_list_field(failures, retrieval, "forbidden_claims", case_id)
        _require_list_field(failures, retrieval, "required_source_domains", case_id)
        _require_list_field(failures, retrieval, "forbidden_source_domains", case_id)
        _require_list_field(failures, final_answer, "required_facts", case_id)
        _require_list_field(failures, final_answer, "forbidden_claims", case_id)
        _require_list_field(failures, final_answer, "required_citation_domains", case_id)
        _require_list_field(failures, final_answer, "forbidden_source_domains", case_id)
        _require_list_field(failures, final_answer, "trust_indicators", case_id)
        _require_list_field(failures, final_answer, "privacy_requirements", case_id)
        if final_answer.get("expected_behavior") not in {
            "answer",
            "clarify",
            "refuse",
            "answer-with-refusal",
        }:
            failures.append(f"case {case_id!r} has invalid expected behavior")

        specs = evaluation_case_assertion_specs(case)
        for spec in specs:
            assertion_id = spec["assertion_id"]
            if assertion_id in assertion_ids:
                failures.append(f"assertion id {assertion_id!r} is duplicated")
            assertion_ids.add(assertion_id)

    missing_behaviors = REQUIRED_BEHAVIOR_CLASSES - behavior_classes
    if missing_behaviors:
        failures.append(
            "evaluation cases are missing behavior classes: "
            f"{', '.join(sorted(missing_behaviors))}"
        )

    missing_content_areas = set(expected_set["required_content_areas"]) - content_areas
    if missing_content_areas:
        failures.append(
            "evaluation cases are missing content areas: "
            f"{', '.join(sorted(missing_content_areas))}"
        )

    return failures


def _validate_quality_bar_shape(quality_bar: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    required_top_level = {
        "quality_bar_id",
        "version",
        "approval_status",
        "issue",
        "parent_prd_issue",
        "prd_user_stories",
        "runtime_baseline",
        "retrieval_baseline",
        "source_governance_baseline",
        "evaluation_set",
        "metrics",
        "thresholds",
        "baseline_results",
        "hardware_targets",
        "environment_matrix",
        "documentation_contract",
    }
    for key in sorted(required_top_level - set(quality_bar)):
        failures.append(f"missing top-level key {key!r}")
    if failures:
        return failures

    if quality_bar["prd_user_stories"] != list(range(75, 87)):
        failures.append("issue #7 quality bar must map to PRD user stories 75-86")

    metric_ids = {
        metric.get("id")
        for metric in quality_bar["metrics"]
        if isinstance(metric, dict)
    }
    missing_metrics = REQUIRED_METRIC_IDS - metric_ids
    if missing_metrics:
        failures.append(f"missing required metrics: {', '.join(sorted(missing_metrics))}")

    return failures


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as json_file:
        payload = json.load(json_file)
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid {label} {path}: expected JSON object")
    return payload


def _strip_markdown_json_fence(text: str) -> str:
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines[0].strip() not in {"```", "```json"}:
        return text
    if lines[-1].strip() != "```":
        raise ValueError("Evaluation quality-bar contract JSON fence is not closed")
    return "\n".join(lines[1:-1]).strip()


def _require_minimum(
    failures: list[str],
    thresholds: dict[str, Any],
    key: str,
    floor: float,
    label: str,
) -> None:
    value = thresholds.get(key)
    if not isinstance(value, int | float) or value < floor:
        failures.append(f"{label} threshold must be at least {floor}")


def _require_maximum(
    failures: list[str],
    thresholds: dict[str, Any],
    key: str,
    ceiling: float,
    label: str,
) -> None:
    value = thresholds.get(key)
    if not isinstance(value, int | float) or value > ceiling:
        failures.append(f"{label} threshold must be no more than {ceiling}")


def _require_list_field(
    failures: list[str],
    payload: dict[str, Any],
    field: str,
    case_id: str | None,
) -> None:
    values = payload.get(field)
    if not isinstance(values, list):
        failures.append(f"case {case_id!r} field {field!r} must be a list")
        return
    if any(not isinstance(value, str) or not value.strip() for value in values):
        failures.append(
            f"case {case_id!r} field {field!r} must contain non-empty text"
        )
