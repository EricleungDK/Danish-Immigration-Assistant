# Issue 7 Evaluation Quality Bar

GitHub issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/7

Parent PRD issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/1

Blocked by:

- https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/2
- https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/4
- https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/6

## Trace

- Machine-readable quality bar: [config/evaluation-quality-bar.json](../../config/evaluation-quality-bar.json)
- Human-readable quality bar: [docs/evaluation-quality-bar.md](../evaluation-quality-bar.md)
- Candidate evaluation set: [data/evaluation/evaluation-set-v0.1-candidate.json](../../data/evaluation/evaluation-set-v0.1-candidate.json)
- Contract tests: [tests/test_evaluation_quality_bar_contract.py](../../tests/test_evaluation_quality_bar_contract.py)
- Runtime baseline: [docs/runtime-baseline.md](../runtime-baseline.md)
- Retrieval recommendation: [docs/progress/issue-29-hybrid-retrieval-recommendation.md](issue-29-hybrid-retrieval-recommendation.md)
- Source governance baseline: [docs/source-governance.md](../source-governance.md)
- Architecture summary: [docs/architecture.md](../architecture.md)
- Live final-answer report: [docs/progress/final-answer-evaluation-live.json](final-answer-evaluation-live.json)
- Hash-bound workflow evidence: [docs/progress/final-answer-machine-evidence/](final-answer-machine-evidence/)

## TDD Record

Issue #7 is primarily a release-quality decision package. The executable seam added here is the public, versioned quality-bar contract:

- Red: `python3 -m unittest tests.test_evaluation_quality_bar_contract -v` failed because `danish_rag.evaluation_quality_bar` did not exist.
- Green: `danish_rag/evaluation_quality_bar.py`, `config/evaluation-quality-bar.json`, `docs/evaluation-quality-bar.md`, and `data/evaluation/evaluation-set-v0.1-candidate.json` were added to validate the dataset shape, release threshold floors, and documentation contract.
- Final evaluator: `danish_rag.final_answer_evaluation` now executes the full
  20-surface contract as 10 live answer cases, four production source-policy
  scenarios, and six automated non-answer workflows. Workflow decisions are
  accepted only with validated SHA-256-bound evidence; semantic answer passes
  require an independent-human-review adjudication bound to the exact
  execution.

## Approved Decisions

- Evaluation dataset candidate: `di-rag-eval-set-v0.1-candidate`, version `0.1.0-candidate`, 20 project-authored synthetic cases.
- Evaluation layers stay separate: retrieval success is measured before generation, and final-answer correctness cannot hide retrieval misses.
- Approved configured release thresholds cover retrieval, citation coverage, unsupported claims, clarify/answer/refuse behavior, Evidence Confidence, Fresh Tomato Score, privacy, rollback, accessibility, reliability, runtime identity, and environment-matrix critical journeys.
- Baseline evidence comes from issue #26 runtime probe and issue #29 hybrid retrieval comparison.
- Minimum hardware candidate remains 16 GB system RAM on Windows 11 with WSL2 Ubuntu, x86-64, Python 3.11+, Ollama 0.30.6+, `gemma4:12b`, `embeddinggemma`, and an evergreen local browser.
- Recommended hardware remains 24 GB RAM when generation and indexing overlap.
- The only verified supported-environment candidate is Windows 11 with WSL2 Ubuntu on x86-64. Native Linux and macOS remain candidates pending full matrix evidence; native Windows is not supported for the MVP candidate.

## Human Approval Record

- Approval status: approved
- Approval record: Product owner approval provided through the initiating GPT goal instruction on 2026-07-13.
- Approver name: not supplied
- Dataset version approved: `di-rag-eval-set-v0.1-candidate` / `0.1.0-candidate`
- Metric definitions approved: the existing definitions in `config/evaluation-quality-bar.json`
- Thresholds approved: the existing configured release thresholds; no absent numeric performance threshold is inferred
- Hardware target approved: the existing 16 GB minimum / 24 GB recommended baseline
- Environment baseline approved: Windows 11 with WSL2 Ubuntu on x86-64 as the only MVP supported candidate; the full critical-journey matrix remains required
- Approval date: 2026-07-13; no more precise timestamp was supplied

This approval removes the decision/sign-off gate only. It does not waive implementation, measurement, performance, accessibility, privacy, security, or release-quality requirements.

## Acceptance Criteria Mapping

- Cases cover happy paths, edge cases, out-of-bounds requests, ambiguity, conflicts, stale sources, refusals, and robustness across every PRD content area: [data/evaluation/evaluation-set-v0.1-candidate.json](../../data/evaluation/evaluation-set-v0.1-candidate.json), all 20 cases.
- Retrieval and final-answer evaluation remain separate and include required facts, forbidden claims, and required or forbidden source domains: candidate dataset fields `retrieval_expectations` and `final_answer_expectations`; [tests/test_evaluation_quality_bar_contract.py](../../tests/test_evaluation_quality_bar_contract.py).
- Proposed thresholds cover retrieval, citations, unsupported claims, clarify/answer/refuse behavior, trust indicators, privacy, rollback, accessibility, and reliability: [config/evaluation-quality-bar.json](../../config/evaluation-quality-bar.json), `metrics` and `thresholds`.
- Hardware and environment targets are supported by baseline measurements: [docs/evaluation-quality-bar.md](../evaluation-quality-bar.md), "Baseline Results", "Hardware Targets", and "Environment Matrix"; [docs/progress/issue-26-runtime-probe.json](issue-26-runtime-probe.json); [docs/progress/issue-29-hybrid-retrieval-comparison.json](issue-29-hybrid-retrieval-comparison.json).
- A human approves and versions the dataset, metrics, thresholds, hardware target, and environment baseline: recorded above and in [docs/evaluation-quality-bar.md](../evaluation-quality-bar.md), "Human Approval Record".
- The implemented runner covers all 20 evaluation surfaces and keeps answer,
  source-policy, and non-answer workflow evidence distinct:
  [docs/progress/final-answer-evaluation-live.json](final-answer-evaluation-live.json),
  `execution` and `case_results`.
- Machine workflow passes require assertion-specific hash-bound artifacts, and
  live answer semantics remain unknown without independent review:
  [docs/progress/final-answer-machine-evidence/](final-answer-machine-evidence/)
  and [docs/evaluation-quality-bar.md](../evaluation-quality-bar.md),
  "Implemented Final-Answer Evaluation".

## Live Evidence Record

At `2026-07-14T18:06:02Z`, the live evaluator completed 20/20 surfaces with
zero execution errors. Clarify/answer/refuse behavior passed 14/14 applicable
cases. Structural, source-domain, official-fact citation-coverage,
trust-indicator, Fresh Tomato, personal-conclusion, and all six automated
workflow gates passed.

Five semantic metrics remain `not_evaluable`: required-fact coverage,
forbidden claims, privacy-requirement compliance, citation correctness for
unreviewed claim/evidence relationships, and unsupported-claim rate. No
independent human adjudication has been supplied, so strict mode correctly
returns nonzero. The evaluator can create a private mode-`0600` packet outside
the repository containing exact synthetic executions and blank review
templates; creating that packet is not human review.

## Remaining Limitations

- Independent adjudication of the 10 live answer cases has not been recorded;
  five semantic release metrics therefore remain `not_evaluable` and the
  strict final-answer gate remains nonzero.
- The current issue #29 retrieval fixture baseline meets the proposed required-evidence Recall@3 threshold on 7 evaluable required-evidence queries, with blocked-source and forbidden-result violations still at 0. It remains baseline fixture evidence, not full release acceptance.
- Numeric performance thresholds remain undefined; approval does not manufacture missing values or release evidence.
- Final-answer evaluation does not substitute for production source admission.
  The current source registry is not production-qualified and has no recorded
  human source review. Maintainers can prepare, but not approve, the private
  blank packet using [docs/source-admission-packet.md](../source-admission-packet.md).
