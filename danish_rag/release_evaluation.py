"""Offline release evaluation report generation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from danish_rag.evaluation_quality_bar import load_evaluation_quality_bar
from danish_rag.evidence_integrity import (
    is_utc_seconds,
    reject_duplicate_json_object,
    sha256_file as _sha256_path,
    utc_now_seconds as _utc_now,
)
from danish_rag.release_qualification import (
    BLOCKING_GATE_STATUSES,
    derive_release_blockers,
    load_release_qualification,
    validate_release_qualification_sources,
)
from danish_rag.runtime_policy import load_runtime_policy
from danish_rag.release_monitors import (
    validate_observed_supported_environment_identity,
)


SCHEMA_VERSION = "1.0"
DEFAULT_OUTPUT_PATH = Path("docs/progress/release-evaluation-current.json")
EVIDENCE_PATHS = {
    "release_qualification": Path("config/release-qualification.json"),
    "quality_bar": Path("config/evaluation-quality-bar.json"),
    "runtime_policy": Path("config/runtime-policy.json"),
    "runtime_probe": Path("docs/progress/issue-26-runtime-probe.json"),
    "lexical_retrieval": Path("docs/progress/issue-27-retrieval-benchmark.json"),
    "dense_retrieval": Path("docs/progress/issue-28-dense-retrieval-benchmark.json"),
    "hybrid_retrieval": Path("docs/progress/issue-29-hybrid-retrieval-comparison.json"),
    "release_progress": Path("docs/progress/issue-25-release-qualification.md"),
    "usability_validation": Path("docs/progress/issue-24-usability-validation.md"),
    "accessibility_browser": Path("docs/progress/issue-23-accessibility-responsive.md"),
    "final_answer_evaluation": Path(
        "docs/progress/final-answer-evaluation-live.json"
    ),
    "release_monitors": Path("docs/progress/release-monitors-live.json"),
}
FINAL_ANSWER_REQUIRED_METRICS = {
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
}
ROLLBACK_FAULT_PHASES = [
    "verification",
    "extraction",
    "embedding",
    "indexing",
    "activation",
    "late_activation",
]
SUPPORTED_ENVIRONMENT_JOURNEYS = [
    "setup",
    "supported-answer",
    "refusal",
    "evidence-inspection",
    "history-persistence",
    "deletion-export",
    "update-installation",
    "rollback",
]
MANUAL_ASSISTIVE_TECHNOLOGY_JOURNEYS = [
    "provider-setup",
    "question-submission",
    "answer-status-announcements",
    "inline-citation-navigation",
    "evidence-drawer-focus-close",
    "history-navigation",
    "update-review",
    "error-recovery",
]
STATUS_SEVERITY = {
    "passed": 0,
    "pending": 1,
    "not_run": 2,
    "not_verified": 2,
    "not_implemented": 3,
    "failed": 4,
    "blocked": 4,
}


class ReleaseEvaluationError(RuntimeError):
    """Raised when release evaluation cannot produce a trustworthy report."""


def generate_release_evaluation(
    repo_root: str | Path,
    *,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root)
    generated_at = generated_at_utc or _utc_now()

    qualification = load_release_qualification(
        root / EVIDENCE_PATHS["release_qualification"]
    )
    quality_bar = load_evaluation_quality_bar(root / EVIDENCE_PATHS["quality_bar"])
    runtime_policy = load_runtime_policy(root / EVIDENCE_PATHS["runtime_policy"])

    source_validation_failures = validate_release_qualification_sources(
        qualification,
        runtime_policy,
        quality_bar,
    )
    evidence_inputs = {
        name: _evidence_ref(root, relative_path)
        for name, relative_path in sorted(EVIDENCE_PATHS.items())
    }

    gate_results = [
        _evaluate_gate(gate, root, quality_bar, runtime_policy)
        for gate in qualification["gate_results"]
    ]
    derived_blockers = derive_release_blockers(qualification)
    strict_release_passed = _strict_release_passed(
        qualification,
        gate_results,
        source_validation_failures=source_validation_failures,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "release_qualification_id": qualification["qualification_id"],
        "release_decision": qualification["release_decision"],
        "qualification_status": qualification["qualification_status"],
        "strict_release_passed": strict_release_passed,
        "config_validation": {
            "release_qualification": [],
            "evaluation_quality_bar": [],
            "runtime_policy": [],
            "source_contract": source_validation_failures,
        },
        "gate_results": gate_results,
        "derived_release_blockers": derived_blockers,
        "evidence_inputs": evidence_inputs,
        "privacy_assertions": {
            "uses_production_user_questions": False,
            "uses_production_answers": False,
            "uses_conversation_identifiers": False,
            "ran_live_network_or_provider_calls": False,
        },
    }


def write_release_evaluation_report(
    report: dict[str, Any], output_path: str | Path
) -> None:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the release evaluation report.")
    parser.add_argument("--repo-root", default=".", help="Repository root to evaluate.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Report path relative to the repository root, or an absolute path.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print report JSON to stdout without writing a file.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero while release-blocking gates remain unpassed.",
    )
    parser.add_argument(
        "--generated-at-utc",
        help="Deterministic UTC timestamp for tests and reproducible reports.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root)
    try:
        report = generate_release_evaluation(
            repo_root,
            generated_at_utc=args.generated_at_utc,
        )
    except Exception as exc:
        print(f"release evaluation failed: {exc}", file=sys.stderr)
        return 2

    if args.no_write:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = repo_root / output_path
        write_release_evaluation_report(report, output_path)

    if args.strict and not report["strict_release_passed"]:
        return 1
    return 0


def _evaluate_gate(
    gate: dict[str, Any],
    root: Path,
    quality_bar: dict[str, Any],
    runtime_policy: dict[str, Any],
) -> dict[str, Any]:
    gate_id = gate["id"]
    source_status = gate["status"]
    evidence: list[dict[str, Any]] = []
    failures: list[str] = []
    observed: dict[str, Any] = {}
    thresholds: dict[str, Any] = {}
    evaluated_status = source_status
    summary = gate.get("summary", "")

    if gate_id == "retrieval-required-evidence-baseline":
        try:
            evaluated_status, summary, observed, thresholds, evidence, failures = (
                _evaluate_retrieval_gate(root, quality_bar)
            )
        except ReleaseEvaluationError as exc:
            if "missing evidence file" not in str(exc):
                raise
            evaluated_status = "not_run"
            summary = "Hybrid retrieval evidence is missing, so the release gate was not run."
            failures = [str(exc)]
            evidence = [_evidence_ref(root, EVIDENCE_PATHS["hybrid_retrieval"])]
    elif _is_final_answer_gate(gate):
        evaluated_status, summary, observed, thresholds, evidence, failures = (
            _evaluate_final_answer_gate(root, quality_bar)
        )
    elif _is_release_privacy_monitor_gate(gate):
        evaluated_status, summary, observed, thresholds, evidence, failures = (
            _evaluate_release_privacy_monitor_gate(
                root,
                quality_bar,
                runtime_policy,
            )
        )
    elif _is_release_rollback_matrix_gate(gate):
        evaluated_status, summary, observed, thresholds, evidence, failures = (
            _evaluate_release_rollback_matrix_gate(root, quality_bar)
        )
    elif _is_supported_environment_gate(gate):
        evaluated_status, summary, observed, thresholds, evidence, failures = (
            _evaluate_supported_environment_gate(root, quality_bar, runtime_policy)
        )
    elif gate_id == "privacy-boundary-fixture":
        evaluated_status, summary, observed, evidence, failures = _evaluate_privacy_gate(
            root,
            runtime_policy,
            gate,
        )
    elif gate_id == "automated-python-regression-suite":
        evidence.append(
            {
                "kind": "manual-command-record",
                "command": ".venv/bin/python -m unittest -v",
            }
        )
    elif gate_id == "browser-accessibility-suite":
        evaluated_status, summary, observed, thresholds, evidence, failures = (
            _evaluate_accessibility_gate(root, quality_bar, gate)
        )

    final_status = _stricter_status(source_status, evaluated_status, failures)
    return {
        "id": gate_id,
        "metric_id": gate.get("metric_id"),
        "status": final_status,
        "release_blocking": gate.get("release_blocking", False),
        "source_status": source_status,
        "evaluated_status": evaluated_status,
        "summary": summary,
        "evidence": evidence,
        "observed": observed,
        "thresholds": thresholds,
        "failures": failures,
    }


def _evaluate_retrieval_gate(
    root: Path,
    quality_bar: dict[str, Any],
) -> tuple[str, str, dict[str, Any], dict[str, Any], list[dict[str, Any]], list[str]]:
    evidence_path = EVIDENCE_PATHS["hybrid_retrieval"]
    evidence = [_evidence_ref(root, evidence_path)]
    data = _load_json_evidence(root / evidence_path)
    selected_candidate = quality_bar["retrieval_baseline"]["selected_candidate"]
    try:
        summary = data["candidates"][selected_candidate]["summary"]
    except KeyError as exc:
        raise ReleaseEvaluationError(
            f"{evidence_path} missing summary for {selected_candidate!r}"
        ) from exc

    retrieval_thresholds = quality_bar["thresholds"]["retrieval"]
    thresholds = {
        "required_evidence_recall_at_3_min": retrieval_thresholds[
            "required_evidence_recall_at_3_min"
        ],
        "blocked_source_violations_max": retrieval_thresholds[
            "blocked_source_violations_max"
        ],
        "forbidden_result_violations_max": retrieval_thresholds[
            "forbidden_result_violations_max"
        ],
    }
    observed = {
        "candidate": selected_candidate,
        "required_evidence_recall_at_3": summary.get("recall_at_3"),
        "required_evidence_query_count": summary.get("required_evidence_query_count"),
        "blocked_source_violations": summary.get("blocked_source_violations"),
        "forbidden_result_violations": summary.get("forbidden_result_violations"),
    }

    failures: list[str] = []
    if (
        observed["required_evidence_query_count"] is None
        or observed["required_evidence_query_count"] <= 0
    ):
        failures.append(
            "hybrid retrieval evidence has no evaluable required-evidence queries"
        )
    if observed["required_evidence_recall_at_3"] is None:
        failures.append("hybrid retrieval evidence is missing recall_at_3")
    elif (
        observed["required_evidence_recall_at_3"]
        < thresholds["required_evidence_recall_at_3_min"]
    ):
        failures.append(
            "hybrid retrieval Recall@3 "
            f"{observed['required_evidence_recall_at_3']} is below "
            f"{thresholds['required_evidence_recall_at_3_min']}"
        )
    if (
        observed["blocked_source_violations"]
        > thresholds["blocked_source_violations_max"]
    ):
        failures.append(
            "hybrid retrieval blocked-source violations "
            f"{observed['blocked_source_violations']} exceed "
            f"{thresholds['blocked_source_violations_max']}"
        )
    if (
        observed["forbidden_result_violations"]
        > thresholds["forbidden_result_violations_max"]
    ):
        failures.append(
            "hybrid retrieval forbidden-result violations "
            f"{observed['forbidden_result_violations']} exceed "
            f"{thresholds['forbidden_result_violations_max']}"
        )

    status = "failed" if failures else "passed"
    text = (
        "Hybrid retrieval required-evidence Recall@3 "
        f"{observed['required_evidence_recall_at_3']} over "
        f"{observed['required_evidence_query_count']} evaluable queries with "
        f"{observed['blocked_source_violations']} blocked-source violations and "
        f"{observed['forbidden_result_violations']} forbidden-result violations."
    )
    return status, text, observed, thresholds, evidence, failures


def _evaluate_accessibility_gate(
    root: Path,
    quality_bar: dict[str, Any],
    gate: dict[str, Any],
) -> tuple[str, str, dict[str, Any], dict[str, Any], list[dict[str, Any]], list[str]]:
    threshold_source = quality_bar["thresholds"]["accessibility"]
    thresholds = {
        "critical_or_serious_automated_violations_max": threshold_source[
            "critical_or_serious_automated_violations_max"
        ],
        "keyboard_core_workflow_pass_rate_min": threshold_source[
            "keyboard_core_workflow_pass_rate_min"
        ],
        "manual_assistive_technology_check_required": threshold_source[
            "manual_assistive_technology_check_required"
        ],
    }
    observed = dict(gate.get("observed") or {})
    evidence: list[dict[str, Any]] = [
        {"kind": "manual-command-record", "command": "npm run test:browser"}
    ]
    failures: list[str] = []

    if observed.get("automated_suite_status") != "current":
        return (
            "not_verified",
            (
                "The current automated browser/accessibility suite has not been "
                "rerun after the latest UI changes, and the required manual "
                "assistive-technology check is also unperformed."
            ),
            observed,
            thresholds,
            evidence,
            failures,
        )

    automated_violations = observed.get(
        "automated_critical_or_serious_violations"
    )
    if (
        not _is_non_negative_int(automated_violations)
        or automated_violations
        > thresholds["critical_or_serious_automated_violations_max"]
    ):
        failures.append(
            "automated accessibility violations are missing or above threshold"
        )
    keyboard_pass_rate = observed.get("keyboard_core_workflow_pass_rate")
    if (
        isinstance(keyboard_pass_rate, bool)
        or not isinstance(keyboard_pass_rate, int | float)
        or keyboard_pass_rate < thresholds["keyboard_core_workflow_pass_rate_min"]
    ):
        failures.append("keyboard core-workflow pass rate is missing or below threshold")

    if failures:
        return "failed", gate.get("summary", ""), observed, thresholds, evidence, failures
    if not thresholds["manual_assistive_technology_check_required"]:
        return "passed", gate.get("summary", ""), observed, thresholds, evidence, failures
    if observed.get("manual_assistive_technology_check") != "passed":
        return (
            "not_verified",
            (
                "Automated accessibility checks pass, but the required manual "
                "assistive-technology check has not been performed or recorded."
            ),
            observed,
            thresholds,
            evidence,
            failures,
        )

    evidence_metadata = gate.get("manual_assistive_technology_evidence")
    if not isinstance(evidence_metadata, dict):
        failures.append("manual assistive-technology evidence metadata is missing")
        return "failed", gate.get("summary", ""), observed, thresholds, evidence, failures

    relative_path = Path(str(evidence_metadata.get("path", "")))
    if (
        relative_path.is_absolute()
        or ".." in relative_path.parts
        or relative_path.parts[:2] != ("docs", "progress")
    ):
        failures.append("manual assistive-technology evidence path is unsafe")
        return "failed", gate.get("summary", ""), observed, thresholds, evidence, failures
    evidence_path = root / relative_path
    evidence.append(_evidence_ref(root, relative_path))
    if not evidence_path.is_file():
        failures.append("manual assistive-technology evidence file is missing")
        return "failed", gate.get("summary", ""), observed, thresholds, evidence, failures
    if evidence_metadata.get("sha256") != _sha256_path(evidence_path):
        failures.append("manual assistive-technology evidence hash does not match")
        return "failed", gate.get("summary", ""), observed, thresholds, evidence, failures

    try:
        payload = _load_json_evidence(evidence_path)
    except ReleaseEvaluationError as exc:
        failures.append(str(exc))
        return "failed", gate.get("summary", ""), observed, thresholds, evidence, failures
    for field in (
        "reviewer_id",
        "assistive_technology",
        "browser",
        "tested_at_utc",
    ):
        if payload.get(field) != evidence_metadata.get(field):
            failures.append(
                f"manual assistive-technology evidence {field} does not match metadata"
            )
    if payload.get("schema_version") != "manual-assistive-technology-v1":
        failures.append("manual assistive-technology evidence schema is unsupported")
    if payload.get("status") != "passed":
        failures.append("manual assistive-technology evidence did not pass")
    if not is_utc_seconds(payload.get("tested_at_utc")):
        failures.append(
            "manual assistive-technology evidence tested_at_utc is not UTC second precision"
        )
    journeys = payload.get("journeys")
    if not isinstance(journeys, list):
        failures.append("manual assistive-technology journeys must be a list")
        journeys = []
    journey_ids = [
        journey.get("id") for journey in journeys if isinstance(journey, dict)
    ]
    if len(journey_ids) != len(journeys) or any(
        not isinstance(journey_id, str) for journey_id in journey_ids
    ):
        failures.append("manual assistive-technology journeys contain invalid entries")
    duplicate_journey_ids = sorted(
        {
            journey_id
            for journey_id in journey_ids
            if isinstance(journey_id, str) and journey_ids.count(journey_id) > 1
        }
    )
    if duplicate_journey_ids:
        failures.append(
            "manual assistive-technology journey IDs are duplicated: "
            + ", ".join(duplicate_journey_ids)
        )
    unknown_journey_ids = sorted(
        {
            journey_id
            for journey_id in journey_ids
            if isinstance(journey_id, str)
            and journey_id not in MANUAL_ASSISTIVE_TECHNOLOGY_JOURNEYS
        }
    )
    if unknown_journey_ids:
        failures.append(
            "manual assistive-technology journey IDs are unknown: "
            + ", ".join(unknown_journey_ids)
        )
    journey_statuses = {
        journey.get("id"): journey.get("status")
        for journey in journeys
        if isinstance(journey, dict)
        and isinstance(journey.get("id"), str)
        and journey.get("id") in MANUAL_ASSISTIVE_TECHNOLOGY_JOURNEYS
    }
    missing_or_failed = [
        journey_id
        for journey_id in MANUAL_ASSISTIVE_TECHNOLOGY_JOURNEYS
        if journey_statuses.get(journey_id) != "passed"
    ]
    if missing_or_failed:
        failures.append(
            "manual assistive-technology journey(s) missing or failed: "
            + ", ".join(missing_or_failed)
        )

    status = "failed" if failures else "passed"
    summary = (
        "Automated accessibility thresholds and the evidence-bound manual "
        "assistive-technology journey set passed."
        if not failures
        else "Manual assistive-technology evidence failed validation."
    )
    return status, summary, observed, thresholds, evidence, failures


def _is_final_answer_gate(gate: dict[str, Any]) -> bool:
    return gate.get("metric_id") == "official-fact-citation-coverage"


def _is_release_privacy_monitor_gate(gate: dict[str, Any]) -> bool:
    return (
        gate.get("metric_id") == "privacy-network-boundary"
        and "fixture" not in str(gate.get("id", ""))
    )


def _is_release_rollback_matrix_gate(gate: dict[str, Any]) -> bool:
    return (
        gate.get("metric_id") == "update-rollback-success"
        and "fixture" not in str(gate.get("id", ""))
    )


def _is_supported_environment_gate(gate: dict[str, Any]) -> bool:
    return gate.get("metric_id") == "environment-matrix-critical-journeys"


def _evaluate_final_answer_gate(
    root: Path,
    quality_bar: dict[str, Any],
) -> tuple[str, str, dict[str, Any], dict[str, Any], list[dict[str, Any]], list[str]]:
    evidence_path = EVIDENCE_PATHS["final_answer_evaluation"]
    data, status, evidence, failures = _load_gate_evidence(root, evidence_path)
    thresholds = dict(quality_bar["thresholds"]["final_answer"])
    if data is None:
        summary = (
            "Live final-answer evaluation evidence is missing."
            if status == "not_run"
            else "Live final-answer evaluation evidence could not be validated."
        )
        return status, summary, {}, thresholds, evidence, failures

    execution = data.get("execution")
    metrics = data.get("metrics")
    threshold_failures = data.get("threshold_failures")
    case_results = data.get("case_results")
    dataset = data.get("dataset")
    report_quality_bar = data.get("quality_bar")
    if data.get("schema_version") != "final-answer-evaluation-v1":
        failures.append("final-answer evidence has an unsupported schema_version")
    expected_evaluation_set = quality_bar["evaluation_set"]
    if not isinstance(dataset, dict):
        failures.append("final-answer evidence is missing dataset identity")
        dataset = {}
    expected_dataset_path = root / expected_evaluation_set["path"]
    if (
        dataset.get("dataset_id") != expected_evaluation_set["dataset_id"]
        or dataset.get("version") != expected_evaluation_set["version"]
        or dataset.get("case_count") != expected_evaluation_set["case_count"]
        or dataset.get("sha256") != _sha256_path(expected_dataset_path)
    ):
        failures.append("final-answer evidence does not match the current dataset")
    if not isinstance(report_quality_bar, dict):
        failures.append("final-answer evidence is missing quality-bar identity")
        report_quality_bar = {}
    if (
        report_quality_bar.get("quality_bar_id") != quality_bar["quality_bar_id"]
        or report_quality_bar.get("version") != quality_bar["version"]
        or report_quality_bar.get("sha256")
        != _sha256_path(root / EVIDENCE_PATHS["quality_bar"])
    ):
        failures.append("final-answer evidence does not match the current quality bar")
    if not isinstance(execution, dict):
        failures.append("final-answer evidence is missing execution metadata")
        execution = {}
    if execution.get("mode") != "live-ollama":
        failures.append("final-answer evidence was not produced in live-ollama mode")
    if execution.get("live_provider_calls") is not True:
        failures.append("final-answer evidence does not confirm live provider calls")

    case_count = execution.get("case_count")
    completed_count = execution.get("completed_count")
    if not _is_positive_int(case_count):
        failures.append("final-answer evidence has no evaluated cases")
    if completed_count != case_count:
        failures.append("final-answer evidence did not complete every case")
    if execution.get("not_evaluable_count") != 0:
        failures.append("final-answer evidence contains not-evaluable cases")
    if execution.get("error_count") != 0:
        failures.append("final-answer evidence contains execution errors")
    if not isinstance(case_results, list) or len(case_results) != case_count:
        failures.append("final-answer case results do not match the execution count")

    metric_statuses: dict[str, Any] = {}
    if not isinstance(metrics, dict):
        failures.append("final-answer evidence is missing metric results")
        metrics = {}
    missing_metrics = sorted(FINAL_ANSWER_REQUIRED_METRICS - set(metrics))
    if missing_metrics:
        failures.append(
            "final-answer evidence is missing required metrics: "
            + ", ".join(missing_metrics)
        )
    for metric_id in sorted(FINAL_ANSWER_REQUIRED_METRICS.intersection(metrics)):
        metric = metrics[metric_id]
        metric_status = metric.get("status") if isinstance(metric, dict) else None
        metric_statuses[metric_id] = (
            metric_status
            if metric_status in {"passed", "failed", "not_evaluable"}
            else "invalid"
        )
        if metric_status != "passed":
            failures.append(
                f"final-answer metric {metric_id!r} did not pass strictly"
            )

    if threshold_failures != []:
        failures.append("final-answer evidence reports threshold failures")
    if data.get("strict_passed") is not True:
        failures.append("final-answer evidence did not pass strict mode")

    observed = {
        "mode": "live-ollama" if execution.get("mode") == "live-ollama" else "invalid",
        "case_count": case_count if _is_non_negative_int(case_count) else None,
        "completed_count": (
            completed_count if _is_non_negative_int(completed_count) else None
        ),
        "not_evaluable_count": _safe_count(execution.get("not_evaluable_count")),
        "error_count": _safe_count(execution.get("error_count")),
        "metric_statuses": metric_statuses,
        "dataset_id": expected_evaluation_set["dataset_id"],
        "quality_bar_id": quality_bar["quality_bar_id"],
        "strict_passed": data.get("strict_passed") is True,
    }
    status = "failed" if failures else "passed"
    summary = (
        "Live final-answer evaluation passed every required metric strictly over "
        f"{case_count} cases."
        if not failures
        else "Live final-answer evaluation evidence did not satisfy the strict release contract."
    )
    return status, summary, observed, thresholds, evidence, failures


def _evaluate_release_privacy_monitor_gate(
    root: Path,
    quality_bar: dict[str, Any],
    runtime_policy: dict[str, Any],
) -> tuple[str, str, dict[str, Any], dict[str, Any], list[dict[str, Any]], list[str]]:
    data, status, evidence, failures = _load_release_monitor_evidence(root)
    thresholds = {
        "answer_time_personal_data_egress_max": quality_bar["thresholds"][
            "privacy"
        ]["answer_time_personal_data_egress_max"]
    }
    if data is None:
        return (
            status,
            _missing_or_invalid_monitor_summary(status),
            {},
            thresholds,
            evidence,
            failures,
        )

    component = data.get("privacy")
    if not isinstance(component, dict):
        failures.append("release-monitor evidence is missing the privacy component")
        component = {}
    if component.get("monitor_id") != "release-network-boundary-monitor":
        failures.append("privacy evidence has an unexpected monitor_id")
    if component.get("mode") != "live":
        failures.append("privacy evidence is not live")
    if component.get("passed") is not True:
        failures.append("privacy monitor did not pass")
    if component.get("failures") != []:
        failures.append("privacy monitor reports failures")
    if component.get("forbidden_request_count") != 0:
        failures.append("privacy monitor observed forbidden network requests")
    release_inspection = component.get("release_request_inspection")
    if not isinstance(release_inspection, dict) or (
        release_inspection.get("content_free") is not True
    ):
        failures.append("privacy monitor did not prove content-free release requests")
    observed_workflows = component.get("observed_workflows")
    required_workflows = runtime_policy["network"][
        "answer_path_observed_workflows"
    ]
    if observed_workflows != required_workflows:
        failures.append("privacy monitor did not observe every required workflow")

    observed = {
        "mode": "live" if component.get("mode") == "live" else "invalid",
        "observed_workflows": [
            workflow
            for workflow in required_workflows
            if isinstance(observed_workflows, list) and workflow in observed_workflows
        ],
        "forbidden_request_count": _safe_count(
            component.get("forbidden_request_count")
        ),
        "release_request_content_free": (
            release_inspection.get("content_free") is True
            if isinstance(release_inspection, dict)
            else False
        ),
        "strict_passed": data.get("strict_passed") is True,
    }
    status = "failed" if failures else "passed"
    summary = (
        "The live privacy monitor observed every required workflow with no forbidden "
        "network requests."
        if not failures
        else "Live privacy-monitor evidence did not satisfy the strict release contract."
    )
    return status, summary, observed, thresholds, evidence, failures


def _evaluate_release_rollback_matrix_gate(
    root: Path,
    quality_bar: dict[str, Any],
) -> tuple[str, str, dict[str, Any], dict[str, Any], list[dict[str, Any]], list[str]]:
    data, status, evidence, failures = _load_release_monitor_evidence(root)
    thresholds = {
        "atomic_update_rollback_success_min": quality_bar["thresholds"]["rollback"][
            "atomic_update_rollback_success_min"
        ]
    }
    if data is None:
        return (
            status,
            _missing_or_invalid_monitor_summary(status),
            {},
            thresholds,
            evidence,
            failures,
        )

    component = data.get("rollback")
    if not isinstance(component, dict):
        failures.append("release-monitor evidence is missing the rollback component")
        component = {}
    if component.get("monitor_id") != "knowledge-release-rollback-fault-matrix":
        failures.append("rollback evidence has an unexpected monitor_id")
    if component.get("mode") != "live":
        failures.append("rollback evidence is not live")
    if component.get("passed") is not True:
        failures.append("rollback fault matrix did not pass")
    if component.get("failures") != []:
        failures.append("rollback fault matrix reports failures")
    results = component.get("results")
    if not isinstance(results, list):
        failures.append("rollback evidence is missing fault-phase results")
        results = []
    if [result.get("phase") for result in results if isinstance(result, dict)] != (
        ROLLBACK_FAULT_PHASES
    ):
        failures.append("rollback evidence does not cover every required fault phase")
    for result in results:
        if not isinstance(result, dict):
            failures.append("rollback evidence contains an invalid phase result")
            continue
        phase = result.get("phase")
        required_true = (
            "signature_verification_passed",
            "signature_rejection_observed",
            "fault_injected",
            "failure_observed",
            "prior_pair_unchanged",
            "prior_pair_queryable",
        )
        if result.get("status") != "passed" or any(
            result.get(field) is not True for field in required_true
        ):
            failures.append(f"rollback phase {phase!r} did not prove safe rollback")
        if result.get("target_release_active") is not False:
            failures.append(f"rollback phase {phase!r} activated the failed release")
        if result.get("installation_reported_success") is not False:
            failures.append(f"rollback phase {phase!r} reported false success")

    passed_phase_count = sum(
        isinstance(result, dict) and result.get("status") == "passed"
        for result in results
    )
    observed = {
        "mode": "live" if component.get("mode") == "live" else "invalid",
        "phase_count": len(results),
        "passed_phase_count": passed_phase_count,
        "success_rate": (
            passed_phase_count / len(ROLLBACK_FAULT_PHASES) if results else 0.0
        ),
        "strict_passed": data.get("strict_passed") is True,
    }
    status = "failed" if failures else "passed"
    summary = (
        "The live rollback fault matrix preserved the prior usable corpus/index pair "
        f"through all {len(ROLLBACK_FAULT_PHASES)} required fault phases."
        if not failures
        else "Live rollback-matrix evidence did not satisfy the strict release contract."
    )
    return status, summary, observed, thresholds, evidence, failures


def _evaluate_supported_environment_gate(
    root: Path,
    quality_bar: dict[str, Any],
    runtime_policy: dict[str, Any],
) -> tuple[str, str, dict[str, Any], dict[str, Any], list[dict[str, Any]], list[str]]:
    data, status, evidence, failures = _load_release_monitor_evidence(root)
    thresholds = {
        "environment_matrix_pass_rate_min": quality_bar["thresholds"]["reliability"][
            "environment_matrix_pass_rate_min"
        ]
    }
    if data is None:
        return (
            status,
            _missing_or_invalid_monitor_summary(status),
            {},
            thresholds,
            evidence,
            failures,
        )

    component = data.get("supported_environment")
    if not isinstance(component, dict):
        failures.append(
            "release-monitor evidence is missing the supported-environment component"
        )
        component = {}
    if component.get("monitor_id") != "supported-environment-critical-journeys":
        failures.append("supported-environment evidence has an unexpected monitor_id")
    if component.get("mode") != "live":
        failures.append("supported-environment evidence is not live")
    if component.get("qualification_scope") != "live-supported-environment":
        failures.append("supported-environment evidence has a non-qualifying scope")
    if component.get("live_provider_calls") is not True:
        failures.append("supported-environment evidence did not use the live provider")
    if component.get("can_qualify_supported_environment") is not True:
        failures.append("supported-environment evidence cannot qualify the environment")
    if component.get("passed") is not True:
        failures.append("supported-environment critical journeys did not pass")
    if component.get("failures") != []:
        failures.append("supported-environment evidence reports failures")
    execution_evidence = component.get("execution_evidence")
    if not isinstance(execution_evidence, dict) or not all(
        (
            execution_evidence.get("transport") == "loopback-bound-process",
            execution_evidence.get("browser_driver") == "playwright",
            _safe_count(execution_evidence.get("browser_phase_count")) is not None
            and execution_evidence["browser_phase_count"] >= 2,
            _safe_count(execution_evidence.get("app_process_start_count")) is not None
            and execution_evidence["app_process_start_count"] >= 2,
            _safe_count(execution_evidence.get("app_process_stop_count")) is not None
            and execution_evidence["app_process_stop_count"] >= 2,
            execution_evidence.get("history_restart_observed") is True,
            execution_evidence.get("browser_evidence_available") is True,
        )
    ):
        failures.append(
            "supported-environment evidence lacks real process, browser, or restart proof"
        )
    observed_environment = component.get("observed_environment_identity")
    required_observed_fields = {
        "host_os",
        "windows_version",
        "windows_build",
        "wsl_version",
        "distribution_id",
        "architecture",
        "python_version",
        "ollama_version",
        "browser_name",
        "browser_version",
    }
    if not isinstance(observed_environment, dict) or any(
        not isinstance(observed_environment.get(field), str)
        or not observed_environment[field]
        for field in required_observed_fields
    ):
        failures.append("supported-environment observed identity is incomplete")
        recomputed_identity_validation = None
    else:
        recomputed_identity_validation = (
            validate_observed_supported_environment_identity(
                observed_environment,
                runtime_policy,
            )
        )
        if recomputed_identity_validation.get("passed") is not True:
            failures.append(
                "supported-environment observed identity fails the current runtime policy"
            )
    identity_validation = component.get("environment_identity_validation")
    identity_checks = (
        identity_validation.get("checks")
        if isinstance(identity_validation, dict)
        else None
    )
    if (
        not isinstance(identity_validation, dict)
        or identity_validation.get("passed") is not True
        or not isinstance(identity_checks, dict)
        or not identity_checks
        or any(check is not True for check in identity_checks.values())
    ):
        failures.append("supported-environment observed identity did not validate")
    elif (
        recomputed_identity_validation is not None
        and identity_checks != recomputed_identity_validation.get("checks")
    ):
        failures.append(
            "supported-environment reported identity checks differ from current validation"
        )
    journeys = component.get("journeys")
    if not isinstance(journeys, list):
        failures.append("supported-environment evidence is missing journey results")
        journeys = []
    if [journey.get("id") for journey in journeys if isinstance(journey, dict)] != (
        SUPPORTED_ENVIRONMENT_JOURNEYS
    ):
        failures.append(
            "supported-environment evidence does not cover every required journey"
        )
    if any(
        not isinstance(journey, dict) or journey.get("status") != "passed"
        for journey in journeys
    ):
        failures.append("one or more supported-environment journeys did not pass")
    for identity_key in (
        "supported_environment_identity",
        "provider_identity",
        "model_identity",
        "corpus_identity",
    ):
        if not isinstance(component.get(identity_key), dict) or not component.get(
            identity_key
        ):
            failures.append(
                f"supported-environment evidence is missing {identity_key}"
            )
    environment_identity = component.get("supported_environment_identity")
    if environment_identity != runtime_policy["supported_environment"][
        "first_verified"
    ]:
        failures.append(
            "supported-environment evidence does not match the runtime policy"
        )
    provider_identity = component.get("provider_identity")
    if not isinstance(provider_identity, dict) or (
        provider_identity.get("provider_id")
        != runtime_policy["providers"]["initial"]["id"]
    ):
        failures.append("supported-environment provider identity does not match policy")
    model_identity = component.get("model_identity")
    if not isinstance(model_identity, dict) or (
        model_identity.get("generation_model")
        != runtime_policy["models"]["generation"]["initial"]
        or model_identity.get("embedding_model")
        != runtime_policy["models"]["embedding"]["initial_supported"]
    ):
        failures.append("supported-environment model identity does not match policy")
    corpus_identity = component.get("corpus_identity")
    required_corpus_identity_fields = {
        "knowledge_release_id",
        "corpus_id",
        "source_registry_version",
        "embedding_model",
        "embedding_vector_dimensions",
        "index_schema_version",
    }
    if not isinstance(corpus_identity, dict) or any(
        corpus_identity.get(field) is None or corpus_identity.get(field) == ""
        for field in required_corpus_identity_fields
    ):
        failures.append("supported-environment corpus identity is incomplete")

    passed_journey_count = sum(
        isinstance(journey, dict) and journey.get("status") == "passed"
        for journey in journeys
    )
    observed = {
        "mode": "live" if component.get("mode") == "live" else "invalid",
        "journey_count": len(journeys),
        "passed_journey_count": passed_journey_count,
        "pass_rate": (
            passed_journey_count / len(SUPPORTED_ENVIRONMENT_JOURNEYS)
            if journeys
            else 0.0
        ),
        "identity_sections_present": [
            identity_key
            for identity_key in (
                "supported_environment_identity",
                "provider_identity",
                "model_identity",
                "corpus_identity",
            )
            if isinstance(component.get(identity_key), dict)
            and bool(component.get(identity_key))
        ],
        "strict_passed": data.get("strict_passed") is True,
    }
    status = "failed" if failures else "passed"
    summary = (
        "The live supported environment passed every release-blocking critical journey."
        if not failures
        else "Supported-environment evidence did not satisfy the strict release contract."
    )
    return status, summary, observed, thresholds, evidence, failures


def _load_release_monitor_evidence(
    root: Path,
) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]], list[str]]:
    data, status, evidence, failures = _load_gate_evidence(
        root,
        EVIDENCE_PATHS["release_monitors"],
    )
    if data is None:
        return data, status, evidence, failures
    if data.get("schema_version") != "1.0":
        failures.append("release-monitor evidence has an unsupported schema_version")
    if data.get("mode") != "live":
        failures.append("release-monitor evidence was not produced in live mode")
    components = [
        data.get("privacy"),
        data.get("rollback"),
        data.get("supported_environment"),
    ]
    computed_component_passed = all(
        isinstance(component, dict) and component.get("passed") is True
        for component in components
    )
    if data.get("component_passed") is not computed_component_passed:
        failures.append(
            "release-monitor component_passed is inconsistent with component results"
        )
    computed_strict_passed = data.get("mode") == "live" and computed_component_passed
    if data.get("strict_passed") is not computed_strict_passed:
        failures.append(
            "release-monitor strict_passed is inconsistent with mode and component results"
        )
    return data, "failed" if failures else "passed", evidence, failures


def _load_gate_evidence(
    root: Path,
    relative_path: Path,
) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]], list[str]]:
    evidence = [_evidence_ref(root, relative_path)]
    try:
        return _load_json_evidence(root / relative_path), "passed", evidence, []
    except (ReleaseEvaluationError, OSError, UnicodeError) as exc:
        status = "not_run" if "missing evidence file" in str(exc) else "failed"
        return None, status, evidence, [str(exc)]


def _missing_or_invalid_monitor_summary(status: str) -> str:
    if status == "not_run":
        return "Live release-monitor evidence is missing."
    return "Live release-monitor evidence could not be validated."


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _safe_count(value: Any) -> int | None:
    return value if _is_non_negative_int(value) else None


def _evaluate_privacy_gate(
    root: Path,
    runtime_policy: dict[str, Any],
    gate: dict[str, Any],
) -> tuple[str, str, dict[str, Any], list[dict[str, Any]], list[str]]:
    evidence_path = EVIDENCE_PATHS["runtime_probe"]
    evidence = [_evidence_ref(root, evidence_path)]
    observed = {
        "answer_path_allows_outbound_requests": runtime_policy["network"][
            "answer_path_allows_outbound_requests"
        ],
    }
    failures: list[str] = []
    if observed["answer_path_allows_outbound_requests"] is not False:
        failures.append("runtime policy allows answer-path outbound requests")
    status = "failed" if failures else gate["status"]
    return status, gate.get("summary", ""), observed, evidence, failures


def _load_json_evidence(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ReleaseEvaluationError(f"missing evidence file: {path}")
    try:
        with path.open(encoding="utf-8") as evidence_file:
            data = json.load(
                evidence_file,
                object_pairs_hook=reject_duplicate_json_object,
            )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ReleaseEvaluationError(
            f"malformed or ambiguous JSON evidence file: {path}"
        ) from exc
    if not isinstance(data, dict):
        raise ReleaseEvaluationError(f"JSON evidence file is not an object: {path}")
    return data


def _evidence_ref(root: Path, relative_path: Path) -> dict[str, Any]:
    path = root / relative_path
    reference: dict[str, Any] = {
        "path": str(relative_path),
        "exists": path.exists(),
    }
    if path.exists() and path.is_file():
        reference["sha256"] = _sha256_path(path)
        reference["size_bytes"] = path.stat().st_size
    return reference


def _stricter_status(source_status: str, evaluated_status: str, failures: list[str]) -> str:
    if failures and evaluated_status == "not_run":
        return "not_run"
    if failures:
        return "failed"
    source_rank = STATUS_SEVERITY.get(source_status, 4)
    evaluated_rank = STATUS_SEVERITY.get(evaluated_status, 4)
    if source_rank >= evaluated_rank:
        return source_status
    return evaluated_status


def _strict_release_passed(
    qualification: dict[str, Any],
    gate_results: list[dict[str, Any]],
    *,
    source_validation_failures: list[str],
) -> bool:
    if source_validation_failures:
        return False
    if qualification["qualification_status"] != "qualified":
        return False
    if qualification["release_decision"] != "release":
        return False
    if derive_release_blockers(qualification):
        return False
    return not any(
        gate["release_blocking"] and gate["status"] in BLOCKING_GATE_STATUSES
        for gate in gate_results
    )


if __name__ == "__main__":
    raise SystemExit(main())
