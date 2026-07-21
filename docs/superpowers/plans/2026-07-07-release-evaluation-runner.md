# Release Evaluation Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `python -m danish_rag.release_evaluation`, an offline release evaluation runner that produces a deterministic report for every published release gate while preserving active blockers.

**Architecture:** Add one focused module, `danish_rag.release_evaluation`, that loads existing release, quality, runtime, and progress evidence through small adapter functions. Keep existing validators authoritative; the runner evaluates evidence and writes a report without mutating release qualification by default.

**Tech Stack:** Python standard library, `unittest`, existing `danish_rag.release_qualification`, `danish_rag.evaluation_quality_bar`, and `danish_rag.runtime_policy` modules.

---

## File Structure

- Create `danish_rag/release_evaluation.py`: report generation, evidence loading, gate evaluation, CLI entrypoint.
- Create `tests/test_release_evaluation.py`: focused unit and CLI tests for the runner.
- Modify `config/release-qualification.json`: replace the coarse runner blocker with a passed runner gate plus precise remaining blockers.
- Modify `docs/release-qualification.md`: update prose and embedded release qualification contract after the config change.
- Modify `docs/progress/issue-25-release-qualification.md`: add the runner report to release qualification evidence and update active blockers.
- Create `docs/progress/release-evaluation-current.json`: generated report from the new runner.

## Task 1: Write Current-Repository Report Tests

**Files:**
- Create: `tests/test_release_evaluation.py`

- [ ] **Step 1: Add failing report generation tests**

Create `tests/test_release_evaluation.py` with this content:

```python
import json
import tempfile
import unittest
from pathlib import Path

from danish_rag.release_evaluation import generate_release_evaluation


ROOT = Path(__file__).resolve().parents[1]


def _gate(report, gate_id):
    return next(gate for gate in report["gate_results"] if gate["id"] == gate_id)


class ReleaseEvaluationReportTests(unittest.TestCase):
    def test_report_covers_current_release_gates_and_keeps_release_blocked(self):
        report = generate_release_evaluation(
            ROOT,
            generated_at_utc="2026-07-07T00:00:00Z",
        )
        qualification = json.loads(
            (ROOT / "config" / "release-qualification.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(report["schema_version"], "1.0")
        self.assertEqual(report["generated_at_utc"], "2026-07-07T00:00:00Z")
        self.assertEqual(
            report["release_qualification_id"],
            qualification["qualification_id"],
        )
        self.assertEqual(report["qualification_status"], "blocked")
        self.assertEqual(report["release_decision"], "do-not-release")
        self.assertFalse(report["strict_release_passed"])

        configured_gate_ids = {gate["id"] for gate in qualification["gate_results"]}
        reported_gate_ids = {gate["id"] for gate in report["gate_results"]}
        self.assertEqual(reported_gate_ids, configured_gate_ids)

        blocker_ids = {blocker["id"] for blocker in report["derived_release_blockers"]}
        self.assertIn("quality-bar-human-approval-pending", blocker_ids)
        self.assertIn("environment-matrix-critical-journeys-not-complete", blocker_ids)
        self.assertNotIn("retrieval-required-evidence-baseline", blocker_ids)

    def test_retrieval_gate_uses_hybrid_evidence_and_quality_bar_thresholds(self):
        report = generate_release_evaluation(
            ROOT,
            generated_at_utc="2026-07-07T00:00:00Z",
        )

        gate = _gate(report, "retrieval-required-evidence-baseline")

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["evaluated_status"], "passed")
        self.assertEqual(gate["observed"]["required_evidence_recall_at_3"], 1.0)
        self.assertEqual(gate["observed"]["required_evidence_query_count"], 7)
        self.assertEqual(gate["observed"]["blocked_source_violations"], 0)
        self.assertEqual(gate["observed"]["forbidden_result_violations"], 0)
        self.assertEqual(gate["thresholds"]["required_evidence_recall_at_3_min"], 0.95)
        self.assertEqual(gate["thresholds"]["blocked_source_violations_max"], 0)
        self.assertEqual(gate["thresholds"]["forbidden_result_violations_max"], 0)
        self.assertEqual(gate["failures"], [])

    def test_report_records_privacy_assertions_without_user_content_fields(self):
        report = generate_release_evaluation(
            ROOT,
            generated_at_utc="2026-07-07T00:00:00Z",
        )

        self.assertEqual(
            report["privacy_assertions"],
            {
                "uses_production_user_questions": False,
                "uses_production_answers": False,
                "uses_conversation_identifiers": False,
                "ran_live_network_or_provider_calls": False,
            },
        )

        serialized = json.dumps(report, sort_keys=True).casefold()
        forbidden_fragments = [
            "production_question_text",
            "production_answer_text",
            "user_question_text",
            "user_answer_text",
            "conversation_id",
            "conversation_record",
        ]
        for fragment in forbidden_fragments:
            self.assertNotIn(fragment, serialized)

    def test_default_output_path_is_not_written_by_core_generator(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "release-evaluation-current.json"

            report = generate_release_evaluation(
                ROOT,
                generated_at_utc="2026-07-07T00:00:00Z",
            )

            self.assertFalse(output_path.exists())
            json.dumps(report, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_release_evaluation -v
```

Expected: fails with `ModuleNotFoundError: No module named 'danish_rag.release_evaluation'`.

- [ ] **Step 3: Commit the red tests**

```bash
git add tests/test_release_evaluation.py
git commit -m "test: add release evaluation report contract"
```

## Task 2: Implement Core Release Evaluation Module

**Files:**
- Create: `danish_rag/release_evaluation.py`
- Test: `tests/test_release_evaluation.py`

- [ ] **Step 1: Add the core module**

Create `danish_rag/release_evaluation.py` with this content:

```python
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

    qualification = load_release_qualification(root / EVIDENCE_PATHS["release_qualification"])
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


def write_release_evaluation_report(report: dict[str, Any], output_path: str | Path) -> None:
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
        evaluated_status, summary, observed, thresholds, evidence, failures = (
            _evaluate_retrieval_gate(root, quality_bar)
        )
    elif gate_id == "privacy-boundary-fixture":
        evaluated_status, summary, observed, evidence, failures = _evaluate_privacy_gate(
            root,
            runtime_policy,
            gate,
        )
    elif gate_id == "automated-python-regression-suite":
        evidence.append({"kind": "manual-command-record", "command": ".venv/bin/python -m unittest -v"})
    elif gate_id == "browser-accessibility-suite":
        evidence.append({"kind": "manual-command-record", "command": "DI_RAG_BROWSER_PORT=8927 npm run test:browser"})

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
    if observed["required_evidence_query_count"] is None or observed["required_evidence_query_count"] <= 0:
        failures.append("hybrid retrieval evidence has no evaluable required-evidence queries")
    if observed["required_evidence_recall_at_3"] is None:
        failures.append("hybrid retrieval evidence is missing recall_at_3")
    elif observed["required_evidence_recall_at_3"] < thresholds["required_evidence_recall_at_3_min"]:
        failures.append(
            "hybrid retrieval Recall@3 "
            f"{observed['required_evidence_recall_at_3']} is below "
            f"{thresholds['required_evidence_recall_at_3_min']}"
        )
    if observed["blocked_source_violations"] != thresholds["blocked_source_violations_max"]:
        failures.append(
            "hybrid retrieval blocked-source violations "
            f"{observed['blocked_source_violations']} exceed zero"
        )
    if observed["forbidden_result_violations"] != thresholds["forbidden_result_violations_max"]:
        failures.append(
            "hybrid retrieval forbidden-result violations "
            f"{observed['forbidden_result_violations']} exceed zero"
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
```

- [ ] **Step 2: Run the current report tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_release_evaluation -v
```

Expected: all 4 tests pass.

- [ ] **Step 3: Commit the core runner**

```bash
git add danish_rag/release_evaluation.py tests/test_release_evaluation.py
git commit -m "feat: add offline release evaluation runner"
```

## Task 3: Add Evidence Failure And CLI Tests

**Files:**
- Modify: `tests/test_release_evaluation.py`
- Modify: `danish_rag/release_evaluation.py`

- [ ] **Step 1: Add fixture helpers and failure tests**

Append these imports near the top of `tests/test_release_evaluation.py`:

```python
import shutil
```

Append this helper below `_gate`:

```python
def _copy_release_fixture(target):
    shutil.copytree(ROOT / "config", target / "config")
    (target / "docs").mkdir()
    shutil.copytree(ROOT / "docs" / "progress", target / "docs" / "progress")
```

Append these tests inside `ReleaseEvaluationReportTests`:

```python
    def test_missing_retrieval_evidence_marks_retrieval_gate_not_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            (
                fixture_root
                / "docs"
                / "progress"
                / "issue-29-hybrid-retrieval-comparison.json"
            ).unlink()

            report = generate_release_evaluation(
                fixture_root,
                generated_at_utc="2026-07-07T00:00:00Z",
            )

            gate = _gate(report, "retrieval-required-evidence-baseline")
            self.assertEqual(gate["status"], "not_run")
            self.assertEqual(gate["evaluated_status"], "not_run")
            self.assertTrue(
                any("missing evidence file" in failure for failure in gate["failures"]),
                gate["failures"],
            )

    def test_malformed_retrieval_evidence_raises_runner_error(self):
        from danish_rag.release_evaluation import ReleaseEvaluationError

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            (
                fixture_root
                / "docs"
                / "progress"
                / "issue-29-hybrid-retrieval-comparison.json"
            ).write_text("{not json", encoding="utf-8")

            with self.assertRaises(ReleaseEvaluationError):
                generate_release_evaluation(
                    fixture_root,
                    generated_at_utc="2026-07-07T00:00:00Z",
                )

    def test_weakened_retrieval_evidence_fails_gate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_root = Path(tmpdir)
            _copy_release_fixture(fixture_root)
            evidence_path = (
                fixture_root
                / "docs"
                / "progress"
                / "issue-29-hybrid-retrieval-comparison.json"
            )
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["candidates"]["hybrid"]["summary"]["recall_at_3"] = 0.5
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

            report = generate_release_evaluation(
                fixture_root,
                generated_at_utc="2026-07-07T00:00:00Z",
            )

            gate = _gate(report, "retrieval-required-evidence-baseline")
            self.assertEqual(gate["status"], "failed")
            self.assertTrue(
                any("below" in failure for failure in gate["failures"]),
                gate["failures"],
            )

    def test_cli_writes_report_and_strict_mode_fails_for_blocked_release(self):
        from danish_rag.release_evaluation import main

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "release-evaluation-current.json"

            default_status = main(
                [
                    "--repo-root",
                    str(ROOT),
                    "--output",
                    str(output_path),
                    "--generated-at-utc",
                    "2026-07-07T00:00:00Z",
                ]
            )
            strict_status = main(
                [
                    "--repo-root",
                    str(ROOT),
                    "--output",
                    str(output_path),
                    "--strict",
                    "--generated-at-utc",
                    "2026-07-07T00:00:00Z",
                ]
            )

            self.assertEqual(default_status, 0)
            self.assertEqual(strict_status, 1)
            self.assertTrue(output_path.exists())
            written_report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(written_report["generated_at_utc"], "2026-07-07T00:00:00Z")
```

- [ ] **Step 2: Adjust the module so missing retrieval evidence becomes `not_run`**

In `danish_rag/release_evaluation.py`, change the retrieval branch in `_evaluate_gate` to this:

```python
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
```

- [ ] **Step 3: Update `_stricter_status` so missing evidence keeps `not_run`**

Replace `_stricter_status` with:

```python
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
```

- [ ] **Step 4: Run the expanded tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_release_evaluation -v
```

Expected: all 8 tests pass.

- [ ] **Step 5: Commit the failure and CLI coverage**

```bash
git add danish_rag/release_evaluation.py tests/test_release_evaluation.py
git commit -m "test: cover release evaluation evidence failures"
```

## Task 4: Update Release Qualification To Use The Runner

**Files:**
- Modify: `config/release-qualification.json`
- Modify: `tests/test_issue_25_release_qualification.py`

- [ ] **Step 1: Replace the coarse runner gate in config**

In `config/release-qualification.json`, replace the gate with id
`full-release-evaluation-runner-not-implemented` with these four gate objects:

```json
{
  "id": "release-evaluation-runner-report",
  "metric_id": "reliability-critical-journeys",
  "status": "passed",
  "summary": "The offline release evaluation runner evaluates every published release gate and writes docs/progress/release-evaluation-current.json without running live provider, browser, or environment-matrix commands by default.",
  "release_blocking": true
},
{
  "id": "final-answer-evaluator-not-implemented",
  "metric_id": "official-fact-citation-coverage",
  "status": "not_implemented",
  "summary": "The final-answer evaluator for citation coverage, citation correctness, unsupported claims, required fact coverage, clarify/answer/refuse behavior, and trust indicators is not implemented.",
  "release_blocking": true
},
{
  "id": "full-network-boundary-monitor-not-implemented",
  "metric_id": "privacy-network-boundary",
  "status": "not_implemented",
  "summary": "The implemented privacy fixtures pass, but a full release monitor for answer-path network egress across the supported critical-journey matrix is not implemented.",
  "release_blocking": true
},
{
  "id": "rollback-fault-injection-matrix-not-implemented",
  "metric_id": "update-rollback-success",
  "status": "not_implemented",
  "summary": "The implemented rollback fixture passes, but the full release fault-injection matrix for installation and activation failures is not implemented.",
  "release_blocking": true
}
```

Keep the existing `environment-matrix-critical-journeys-not-complete` gate as the supported-environment matrix blocker.

- [ ] **Step 2: Update issue 25 blocker expectations**

In `tests/test_issue_25_release_qualification.py`, replace the blocker subset in
`test_release_qualification_reports_blocked_release_without_weakening_gates`
with:

```python
            {
                "quality-bar-human-approval-pending",
                "final-answer-evaluator-not-implemented",
                "full-network-boundary-monitor-not-implemented",
                "rollback-fault-injection-matrix-not-implemented",
                "environment-matrix-critical-journeys-not-complete",
                "performance-thresholds-not-approved",
                "issue-24-human-validation-pending",
            }.issubset(blocker_ids),
```

Add this assertion after the existing `assertNotIn` retrieval assertion:

```python
        self.assertNotIn("full-release-evaluation-runner-not-implemented", blocker_ids)
        runner_gate = next(
            gate
            for gate in qualification["gate_results"]
            if gate["id"] == "release-evaluation-runner-report"
        )
        self.assertEqual(runner_gate["status"], "passed")
```

- [ ] **Step 3: Run release qualification and runner tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_issue_25_release_qualification tests.test_release_evaluation -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit the release qualification gate split**

```bash
git add config/release-qualification.json tests/test_issue_25_release_qualification.py
git commit -m "docs: split release evaluation runner blockers"
```

## Task 5: Generate Report And Update Release Documentation

**Files:**
- Create: `docs/progress/release-evaluation-current.json`
- Modify: `docs/release-qualification.md`
- Modify: `docs/progress/issue-25-release-qualification.md`

- [ ] **Step 1: Generate the report**

Run:

```bash
.venv/bin/python -m danish_rag.release_evaluation --generated-at-utc 2026-07-07T00:00:00Z
```

Expected: command exits `0` and writes `docs/progress/release-evaluation-current.json`.

- [ ] **Step 2: Confirm strict mode blocks release**

Run:

```bash
.venv/bin/python -m danish_rag.release_evaluation --strict --no-write --generated-at-utc 2026-07-07T00:00:00Z
```

Expected: command exits `1` because release-blocking gates remain.

- [ ] **Step 3: Update `docs/progress/issue-25-release-qualification.md` trace and blockers**

Add this bullet to the Trace list:

```markdown
- Current release evaluation report: [docs/progress/release-evaluation-current.json](../../progress/release-evaluation-current.json)
```

Replace the active blocker bullet that says the full production release evaluation runner is not implemented with:

```markdown
- Offline release evaluation runner is implemented and publishes `docs/progress/release-evaluation-current.json`; release remains blocked by the missing final-answer evaluator, full network-boundary monitor, rollback fault-injection matrix, and full supported-environment critical journey matrix.
```

- [ ] **Step 4: Update `docs/release-qualification.md`**

In the evaluation results and limitations section, add a sentence with this exact content:

```markdown
The offline release evaluation runner publishes `docs/progress/release-evaluation-current.json` and evaluates every published release gate without running live provider, browser, or environment-matrix commands by default.
```

In the active blockers prose, replace the coarse runner blocker with this exact sentence:

```markdown
Release remains blocked by missing final-answer evaluation, full network-boundary monitoring, rollback fault-injection coverage, supported-environment matrix completion, human approval, and approved performance thresholds.
```

If the embedded release qualification contract changes because `config/release-qualification.json` changed, update the contract block so `tests.test_issue_25_release_qualification` passes.

- [ ] **Step 5: Run documentation contract tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_issue_25_release_qualification tests.test_release_evaluation -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit generated report and docs**

```bash
git add docs/progress/release-evaluation-current.json docs/release-qualification.md docs/progress/issue-25-release-qualification.md
git commit -m "docs: publish release evaluation report"
```

## Task 6: Final Verification

**Files:**
- Verify current worktree and generated artifacts.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_release_evaluation tests.test_issue_25_release_qualification tests.test_evaluation_quality_bar_contract tests.test_retrieval_benchmark -v
```

Expected: all focused tests pass.

- [ ] **Step 2: Run the full Python suite**

Run:

```bash
.venv/bin/python -m unittest -v
```

Expected: all non-live tests pass; live dense benchmark skips by default.

- [ ] **Step 3: Run browser regression suite**

Run:

```bash
DI_RAG_BROWSER_PORT=8927 npm run test:browser
```

Expected: all browser tests pass. Use unsandboxed execution if the sandbox blocks local socket binding.

- [ ] **Step 4: Confirm runner report remains blocked and complete**

Run:

```bash
.venv/bin/python -m danish_rag.release_evaluation --no-write --generated-at-utc 2026-07-07T00:00:00Z
```

Expected output includes:

```json
"qualification_status": "blocked"
```

Expected output includes:

```json
"release_decision": "do-not-release"
```

Expected output includes all gate ids from `config/release-qualification.json`.

- [ ] **Step 5: Check unrelated dirty files before final response**

Run:

```bash
git status --short
```

Expected: new runner changes are visible; unrelated dirty files from the handoff remain preserved.
