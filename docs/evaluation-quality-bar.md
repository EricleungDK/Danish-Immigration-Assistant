# Evaluation Quality Bar

This document records the issue #7 evaluation-set candidate, metric definitions, approved release-blocking thresholds, hardware target, and supported-environment baseline for Danish Immigration RAG.

The machine-readable source of truth is [config/evaluation-quality-bar.json](../config/evaluation-quality-bar.json). The embedded contract below is checked by tests so documentation drift is visible.

## Approval Status

Status: `approved`.

Product owner approval provided through the initiating GPT goal instruction on 2026-07-13. The approval covers the existing dataset, metric definitions, configured release thresholds, hardware target, and supported-environment baseline. It removes the decision gate only: implementation, measurement, accessibility, security, privacy, performance, and release-test requirements still have to pass. No missing numeric performance threshold is inferred from this approval. Thresholds must not be weakened silently to pass a build; any weakening requires a new quality-bar version and recorded human approval.

## Policy Contract

<!-- evaluation-quality-bar-contract:start -->
```json
{
  "quality_bar_id": "mvp-evaluation-quality-bar-issue-7",
  "version": "0.1.0-candidate",
  "approval_status": "approved",
  "dataset_id": "di-rag-eval-set-v0.1-candidate",
  "dataset_version": "0.1.0-candidate",
  "dataset_case_count": 20,
  "runtime_baseline": "mvp-runtime-baseline-issue-26",
  "generation_model": "gemma4:12b",
  "embedding_model": "embeddinggemma",
  "retrieval_baseline": "hybrid",
  "retrieval_required_evidence_recall_at_3_min": 0.95,
  "official_fact_citation_coverage_min": 1.0,
  "unsupported_claim_rate_max": 0.0,
  "answer_time_personal_data_egress_max": 0,
  "accessibility_standard": "WCAG 2.2 AA"
}
```
<!-- evaluation-quality-bar-contract:end -->

## Trace

- GitHub issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/7
- Parent PRD: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/1
- Candidate dataset: [data/evaluation/evaluation-set-v0.1-candidate.json](../data/evaluation/evaluation-set-v0.1-candidate.json)
- Runtime baseline: [docs/runtime-baseline.md](runtime-baseline.md), [config/runtime-policy.json](../config/runtime-policy.json)
- Retrieval baseline: [docs/progress/issue-29-hybrid-retrieval-comparison.md](progress/issue-29-hybrid-retrieval-comparison.md), [docs/progress/issue-29-hybrid-retrieval-recommendation.md](progress/issue-29-hybrid-retrieval-recommendation.md)
- Source governance baseline: [docs/source-governance.md](source-governance.md)
- Issue #7 progress and approval record: [docs/progress/issue-7-evaluation-quality-bar.md](progress/issue-7-evaluation-quality-bar.md)

## Evaluation Set Candidate

The candidate dataset is `di-rag-eval-set-v0.1-candidate` with 20 project-authored synthetic cases. It is not copied from webpages and is not derived from user conversation records.

Required behavior classes:

- Happy paths
- Edge cases
- Out-of-bounds requests
- Ambiguity
- Conflicts
- Stale sources
- Refusals
- Robustness

Required content areas:

- Permanent-residence language requirements
- Danish examination types
- Registration logistics
- Certificate and equivalence boundaries
- Source boundaries
- Ambiguity handling
- Conflicts and stale sources
- Refusals and unsupported claims
- Accessibility and responsive workflows
- Reliability and recovery
- Runtime identity
- Privacy and update telemetry

Retrieval evaluation and final-answer evaluation remain separate. Each case records required facts, forbidden claims, required or forbidden source domains, allowed or blocked source states, expected answer behavior, citation requirements, trust-indicator expectations, and privacy requirements.

## Implemented Final-Answer Evaluation

The production evaluator is implemented in
[`danish_rag.final_answer_evaluation`](../danish_rag/final_answer_evaluation.py).
It covers all 20 dataset surfaces without treating every surface as a generated
answer:

- In `live-ollama` mode, 10 answer-path cases call the configured local Ollama
  generation model and evaluate the resulting answer, citations, source
  identities, and trust indicators.
- Four source-policy scenarios exercise production `AnswerService` behavior for
  source conflict, overdue-policy usability, changed-source blocking, and a
  retrieval miss that generation must not mask.
- Six non-answer workflow cases use automated evidence: three focused browser
  workflows, two knowledge-release workflows, and one provider-recovery
  workflow.

Automated workflow adjudications are accepted only when they bind each
assertion to a validated workflow artifact and its exact SHA-256 digest. Those
artifacts in turn bind the reports or test sources they consumed, so a changed
report or implementation cannot silently reuse an earlier machine pass.
Machine workflow evidence never represents itself as human assessment.

Semantic answer review is deliberately separate. With
`--human-review-packet`, the evaluator writes the exact synthetic prompts,
generated answers, retrieved evidence, assertion IDs, execution hashes, and
blank adjudication templates to a local-only file outside the repository. The
file is created with mode `0600`. It contains no human decisions until an
independent reviewer fills the templates; the public evaluation report never
embeds the packet.

Run the live monitors first, using the application's default XDG configuration
and data locations:

```bash
.venv/bin/python -B -m danish_rag.release_monitors \
  --mode live \
  --output docs/progress/release-monitors-live.json \
  --generated-at-utc "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --strict
```

Then execute the full evaluation and generate the six workflow artifacts:

```bash
.venv/bin/python -B -m danish_rag.final_answer_evaluation \
  --mode live-ollama \
  --output docs/progress/final-answer-evaluation-live.json \
  --generate-automated-evidence docs/progress/final-answer-machine-evidence \
  --release-monitor-report docs/progress/release-monitors-live.json \
  --human-review-packet /tmp/danish-rag-final-answer-human-review.json \
  --generated-at-utc "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --strict
```

No `--config-path` or `--data-dir` override is present: the command uses the
normal per-user XDG paths. Until independent answer adjudications are supplied
with `--adjudications`, strict mode is expected to return status 1 rather than
turn unknown semantic results into passes.

## Release-Blocking Metrics

The approved release-blocking metrics are:

| Area | Metric | Threshold |
| --- | --- | --- |
| Retrieval | Required evidence Recall@3 | At least 0.95 |
| Retrieval | Critical guardrail Recall@3 | 1.0 |
| Retrieval | Blocked-source violations | 0 |
| Retrieval | Forbidden-result violations | 0 |
| Final answer | Official-fact citation coverage | 1.0 |
| Final answer | Citation correctness | 1.0 |
| Final answer | Unsupported-claim rate | 0.0 |
| Final answer | Required-fact coverage when evidence exists | At least 0.95 |
| Final answer | Clarify/answer/refuse behavior accuracy | At least 0.95 |
| Final answer | Trust-indicator correctness | At least 0.95 |
| Privacy | Answer-time personal-data egress | 0 |
| Rollback | Atomic update rollback success | 1.0 |
| Accessibility | WCAG conformance | WCAG 2.2 AA, no critical or serious automated violations, full keyboard workflow, and required manual assistive-technology check |
| Reliability | Critical journey pass rate | 1.0 across every published supported environment |

Any personal eligibility conclusion, unsupported legal claim, answer-time network egress containing user content, blocked source used as material evidence, or uncited official fact is a release-blocking failure.

## Baseline Results

The approved configured thresholds are release thresholds, not a claim that the current fixture benchmark already satisfies every release gate.

Current evidence from the approved runtime and retrieval decisions:

- Runtime probe passed on Windows 11 with WSL2 Ubuntu, Python 3.12.3, Ollama 0.30.6, and `gemma4:12b`.
- The probe host recorded x86-64, 16 CPU threads, and 15908 MB RAM.
- Structured completion took 25805.935 ms in the issue #26 probe.
- The issue #29 comparison selected hybrid retrieval with `embeddinggemma`, vector dimensions 768, and reciprocal-rank fusion with `k=60`.
- The comparison recorded hybrid required-evidence Recall@3 1.0 and MRR 1.0 across 7 evaluable required-evidence queries, with blocked-source violations 0 and forbidden-result violations 0 on the reviewed 9-query benchmark fixture set.
- Dense retrieval evidence in the comparison recorded mean query latency 146.602 ms, mean warm retrieval latency 64.659 ms, dense indexing wall time 1408.696 ms, index size 151360 bytes, and process peak resident memory 101.434 MB.

The release-quality dataset is broader than the issue #29 retrieval fixture set. The current retrieval numbers are baselines that justify the hardware and environment candidate, not full release acceptance results.

## Final-Answer Evidence, 2026-07-14

The live report generated at `2026-07-14T18:06:02Z` is
[docs/progress/final-answer-evaluation-live.json](progress/final-answer-evaluation-live.json).
It records:

- 20 of 20 evaluation surfaces completed and zero execution errors;
- all 10 answer-path cases executed through the live local provider;
- clarify/answer/refuse behavior passed on all 14 applicable answer and
  source-policy cases;
- official-fact citation coverage 35/35, required source-domain coverage
  11/11, and zero forbidden-domain violations;
- trust indicators passed on 20/20 surfaces, the minimum-material-source Fresh
  Tomato rule passed on all 13 applicable cases, and zero personal eligibility
  conclusions were detected; and
- all six hash-bound automated workflows passed.

Five release-blocking semantic metrics are `not_evaluable`, not failed or
passed: required-fact coverage, forbidden claims, privacy-requirement
compliance, citation correctness for relationships needing review, and
unsupported-claim rate. The machine run recorded no known unsupported or
incorrect relationships, but independent human adjudication is absent, so the
evaluator does not infer their absence. Consequently `strict_passed` is false.
This report is comprehensive machine execution evidence, not a claim of human
review or production qualification.

## Hardware Targets

Minimum supported candidate:

- Windows 11 with WSL2 Ubuntu
- x86-64
- Python 3.11 or newer
- Ollama 0.30.6 or newer
- `gemma4:12b` Q4_K_M
- `embeddinggemma`
- 16 GB system RAM
- Evergreen local browser

Recommended:

- 24 GB system RAM when local generation and indexing overlap
- GPU acceleration where available

CPU-only compatibility and latency are measured rather than guaranteed. Any environment published as supported must pass the full issue #7 critical journey matrix before release.

## Environment Matrix

| Environment | Status | Release gate |
| --- | --- | --- |
| Windows 11 with WSL2 Ubuntu on x86-64 | MVP supported verified candidate | Full issue #7 critical journey matrix before release |
| Native Linux on x86-64 | Candidate, not verified | Runtime probe, retrieval benchmark, answer/refusal, history, evidence, accessibility, and rollback tests |
| macOS Apple Silicon | Candidate, not verified | Provider/model setup, local indexing, browser, accessibility, and rollback tests |
| Native Windows | Not supported for MVP candidate | Separate launch, storage, provider, and browser-security verification path |

## Human Approval Record

- Approval required: yes
- Status: approved
- Approval record: Product owner approval provided through the initiating GPT goal instruction on 2026-07-13.
- Approved version: `0.1.0-candidate` as currently configured
- Approver name: not supplied
- Approval date: 2026-07-13; no more precise timestamp was supplied

This record does not claim that any release gate passed and does not approve values that are absent from the quality-bar package.
