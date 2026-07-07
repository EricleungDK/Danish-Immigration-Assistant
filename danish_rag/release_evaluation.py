"""Offline release evaluation report generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from danish_rag.evaluation_quality_bar import load_evaluation_quality_bar
from danish_rag.release_qualification import (
    BLOCKING_GATE_STATUSES,
    derive_release_blockers,
    load_release_qualification,
    validate_release_qualification_sources,
)
from danish_rag.runtime_policy import load_runtime_policy


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
}
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
    strict_release_passed = _strict_release_passed(qualification, gate_results)

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
        evidence.append(
            {
                "kind": "manual-command-record",
                "command": "DI_RAG_BROWSER_PORT=8927 npm run test:browser",
            }
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
            data = json.load(evidence_file)
    except json.JSONDecodeError as exc:
        raise ReleaseEvaluationError(f"malformed JSON evidence file: {path}") from exc
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
        data = path.read_bytes()
        reference["sha256"] = hashlib.sha256(data).hexdigest()
        reference["size_bytes"] = len(data)
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
) -> bool:
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


if __name__ == "__main__":
    raise SystemExit(main())
