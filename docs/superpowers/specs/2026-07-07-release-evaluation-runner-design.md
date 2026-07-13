# Release Evaluation Runner Design

Date: 2026-07-07

## Context

The project has a blocked MVP release qualification in
`config/release-qualification.json`. Existing modules validate the release
qualification, quality bar, runtime policy, retrieval benchmarks, runtime
probe evidence, and release documentation. The remaining release blockers are
mostly governance and coverage gaps, but the release qualification currently
has no single runner that evaluates all published gates and produces a
machine-readable release evaluation report.

The runner must preserve the current release posture. It should make blockers
more explicit, not turn a blocked release into a qualified one.

## Goals

- Provide one local command for release evaluation:
  `python -m danish_rag.release_evaluation`.
- Evaluate every gate published in `config/release-qualification.json`.
- Read existing evidence and configuration instead of re-running live browser,
  Ollama, network, or environment-matrix checks by default.
- Produce deterministic JSON output with gate statuses, evidence references,
  validation failures, and derived release blockers.
- Keep quality thresholds at least as strict as
  `config/evaluation-quality-bar.json`.
- Keep human approval, unsupported environment, performance threshold, final
  answer evaluator, and missing matrix evidence as blockers until their own
  evidence exists.
- Avoid production-user questions, answers, conversation IDs, or other user
  data in the report.

## Non-Goals

- Do not implement the final-answer grader in this slice.
- Do not execute live Ollama, browser, accessibility, or matrix commands by
  default.
- Do not rewrite `config/release-qualification.json` automatically.
- Do not approve the quality bar or performance thresholds.
- Do not mark the MVP release as qualified while active blockers remain.

## Chosen Approach

Add a new module, `danish_rag.release_evaluation`, with a small CLI and
testable evaluation functions. The runner is an evidence aggregator first. It
loads the release qualification, quality bar, runtime policy, and current
progress evidence, then emits one report that explains which gates passed,
which gates are pending, which gates are not implemented, and which evidence
supports each decision.

This is a full release evaluation runner for the current release contract: each
published gate receives a current status and evidence trail. Later work can add
command orchestration behind explicit flags without changing the report schema.

## Command Interface

Default command:

```bash
python -m danish_rag.release_evaluation
```

Default behavior:

- Reads from the repository root.
- Writes `docs/progress/release-evaluation-current.json`.
- Exits `0` when the evaluation completes and the report truthfully records a
  blocked release.
- Exits non-zero only for runner failures, invalid configuration, malformed
  evidence, or a contradictory report.

Supported options:

- `--repo-root PATH`: evaluate a different checkout or fixture root.
- `--output PATH`: write the report somewhere else.
- `--no-write`: print the report JSON to stdout without writing a file.
- `--strict`: return non-zero when any release-blocking gate is not passed.

The default exit code separates "the runner worked and found blockers" from
"the runner failed." `--strict` is available for CI jobs that should fail while
release blockers remain.

## Report Schema

The JSON report should include:

- `schema_version`
- `generated_at_utc`
- `release_qualification_id`
- `release_decision`
- `qualification_status`
- `strict_release_passed`
- `config_validation`
- `gate_results`
- `derived_release_blockers`
- `evidence_inputs`
- `privacy_assertions`

Each gate result should include:

- `id`
- `metric_id`
- `status`
- `release_blocking`
- `source_status`
- `evaluated_status`
- `summary`
- `evidence`
- `failures`

`source_status` records the status published in
`config/release-qualification.json`. `evaluated_status` records the runner's
status after checking available evidence. The final `status` is the stricter of
the two when evidence is missing or contradictory.

## Evidence Inputs

Initial adapters:

- Release qualification:
  `config/release-qualification.json`
- Evaluation quality bar:
  `config/evaluation-quality-bar.json`
- Runtime policy:
  `config/runtime-policy.json`
- Runtime probe evidence:
  `docs/progress/issue-26-runtime-probe.json`
- Lexical retrieval benchmark:
  `docs/progress/issue-27-retrieval-benchmark.json`
- Dense retrieval benchmark:
  `docs/progress/issue-28-dense-retrieval-benchmark.json`
- Hybrid retrieval comparison:
  `docs/progress/issue-29-hybrid-retrieval-comparison.json`
- Release qualification progress:
  `docs/progress/issue-25-release-qualification.md`
- Human usability validation progress, if present:
  `docs/progress/issue-24-usability-validation.md`
- Accessibility and browser evidence, if present:
  `docs/progress/issue-23-accessibility-responsive.md`

Missing optional evidence does not crash the runner. It becomes `not_run`,
`not_implemented`, or `pending` on the affected gate with a clear failure
message.

## Gate Evaluation Rules

The runner starts from the gates in `config/release-qualification.json` and
applies evidence-specific checks.

- Configuration gates pass only when release qualification, quality bar, runtime
  policy, and documentation-source checks are valid.
- Retrieval gates pass only when the selected hybrid evidence meets the
  required-evidence Recall@3 threshold and has zero blocked-source and
  forbidden-result violations.
- Runtime probe evidence must match the runtime policy and must not imply
  answer-path network egress.
- Browser, accessibility, rollback, and privacy gates may retain their source
  status when the release qualification already publishes a passed gate, but the
  runner must record missing machine evidence as an evidence gap.
- Human approval gates remain `pending` until the release qualification records
  approved human approval records.
- Performance remains `pending` until approved thresholds exist and all required
  supported-environment measurements are present.
- Environment-matrix gates remain `not_run` until every published supported
  environment has the required critical journeys recorded.
- Final-answer evaluation remains `not_implemented` until an implemented grader
  and evidence report exist.

If a configured gate says `passed` but required evidence contradicts it, the
runner reports the gate as `failed` and includes the contradiction in
`failures`.

## Privacy And Safety

The report must not include production user content. The runner should only read
checked-in configs, checked-in evaluation fixtures, and progress evidence. It
should include a `privacy_assertions` section stating:

- production user questions were not used;
- production answers were not used;
- conversation identifiers were not used;
- no live network or local provider call was made by default.

## Tests

Add unit tests for:

- report generation from the current repository evidence;
- deterministic output when volatile timestamp fields are controlled;
- missing evidence becomes a blocking `not_run` or `pending` gate;
- malformed evidence produces a runner failure;
- retrieval thresholds are enforced from the quality bar;
- blocked-source and forbidden-result violations fail retrieval gates;
- pending human approvals keep the release blocked;
- `--strict` returns non-zero when release-blocking gates remain;
- default command returns success when it truthfully writes a blocked report;
- report output contains no production-user question, answer, or conversation
  fields.

## Documentation Updates

After implementation, update:

- `docs/progress/issue-25-release-qualification.md`
- `docs/release-qualification.md`
- `config/release-qualification.json`

The release qualification should stop using the coarse
`full-release-evaluation-runner-not-implemented` blocker once this runner is in
place. It should replace that blocker with more precise remaining blockers for
missing final-answer evaluation, full network-boundary monitoring, rollback
fault-injection coverage, and supported-environment matrix coverage.

## Acceptance Criteria

- `python -m danish_rag.release_evaluation` writes a deterministic report.
- The report covers every gate in `config/release-qualification.json`.
- Active blockers remain explicit and release-blocking.
- Retrieval evidence remains at or above the approved candidate threshold.
- The release remains `blocked` and `do-not-release` while pending blockers
  exist.
- Existing unit and browser tests continue to pass.
