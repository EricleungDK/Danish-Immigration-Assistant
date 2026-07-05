# Evaluation Quality Bar

This document records the issue #7 evaluation-set candidate, metric definitions, proposed release-blocking thresholds, hardware targets, and supported-environment matrix for Danish Immigration RAG.

The machine-readable source of truth is [config/evaluation-quality-bar.json](../config/evaluation-quality-bar.json). The embedded contract below is checked by tests so documentation drift is visible.

## Approval Status

Status: `candidate-ready-for-human-approval`.

This is a reviewed agent draft, not final human approval. The dataset, metric definitions, thresholds, hardware targets, and environment matrix must be approved by a human before they can become the release-blocking MVP quality bar. Thresholds must not be weakened silently to pass a build; any weakening requires a new quality-bar version and a recorded human approval.

## Policy Contract

<!-- evaluation-quality-bar-contract:start -->
```json
{
  "quality_bar_id": "mvp-evaluation-quality-bar-issue-7",
  "version": "0.1.0-candidate",
  "approval_status": "candidate-ready-for-human-approval",
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

## Release-Blocking Metrics

The proposed release-blocking metrics are:

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
| Accessibility | WCAG conformance | WCAG 2.2 AA, no critical or serious automated violations |
| Reliability | Critical journey pass rate | 1.0 across every published supported environment |

Any personal eligibility conclusion, unsupported legal claim, answer-time network egress containing user content, blocked source used as material evidence, or uncited official fact is a release-blocking failure.

## Baseline Results

The proposed thresholds are release thresholds, not a claim that the current fixture benchmark already satisfies every release gate.

Current evidence from the approved runtime and retrieval decisions:

- Runtime probe passed on Windows 11 with WSL2 Ubuntu, Python 3.12.3, Ollama 0.30.6, and `gemma4:12b`.
- The probe host recorded x86-64, 16 CPU threads, and 15908 MB RAM.
- Structured completion took 25805.935 ms in the issue #26 probe.
- The issue #29 comparison selected hybrid retrieval with `embeddinggemma`, vector dimensions 768, and reciprocal-rank fusion with `k=60`.
- The comparison recorded hybrid Recall@3 0.777778, MRR 0.777778, blocked-source violations 0, and forbidden-result violations 0 on the reviewed benchmark fixture set.
- Dense retrieval evidence recorded mean query latency 128.253 ms, mean warm retrieval latency 48.791 ms, dense indexing wall time 4714.887 ms, index size 151360 bytes, and process peak resident memory 87.492 MB.

The release-quality dataset is broader than the issue #29 retrieval fixture set. The current retrieval numbers are baselines that justify the hardware and environment candidate, not release acceptance results.

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
- Status: pending
- Approver: TBD
- Approved version: TBD
- Approved at UTC: TBD

Do not treat `0.1.0-candidate` as the approved release-blocking quality bar until this record is completed by a human approver.
