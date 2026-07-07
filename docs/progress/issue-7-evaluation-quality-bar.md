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

## TDD Record

Issue #7 is primarily a release-quality decision package. The executable seam added here is the public, versioned quality-bar contract:

- Red: `python3 -m unittest tests.test_evaluation_quality_bar_contract -v` failed because `danish_rag.evaluation_quality_bar` did not exist.
- Green: `danish_rag/evaluation_quality_bar.py`, `config/evaluation-quality-bar.json`, `docs/evaluation-quality-bar.md`, and `data/evaluation/evaluation-set-v0.1-candidate.json` were added to validate the dataset shape, release threshold floors, and documentation contract.

## Drafted Decisions

- Evaluation dataset candidate: `di-rag-eval-set-v0.1-candidate`, version `0.1.0-candidate`, 20 project-authored synthetic cases.
- Evaluation layers stay separate: retrieval success is measured before generation, and final-answer correctness cannot hide retrieval misses.
- Proposed release thresholds cover retrieval, citation coverage, unsupported claims, clarify/answer/refuse behavior, Evidence Confidence, Fresh Tomato Score, privacy, rollback, accessibility, reliability, runtime identity, and environment-matrix critical journeys.
- Baseline evidence comes from issue #26 runtime probe and issue #29 hybrid retrieval comparison.
- Minimum hardware candidate remains 16 GB system RAM on Windows 11 with WSL2 Ubuntu, x86-64, Python 3.11+, Ollama 0.30.6+, `gemma4:12b`, `embeddinggemma`, and an evergreen local browser.
- Recommended hardware remains 24 GB RAM when generation and indexing overlap.
- The only verified supported-environment candidate is Windows 11 with WSL2 Ubuntu on x86-64. Native Linux and macOS remain candidates pending full matrix evidence; native Windows is not supported for the MVP candidate.

## Human Approval Record

- Approval status: pending
- Approver: TBD
- Dataset version approved: TBD
- Metric definitions approved: TBD
- Thresholds approved: TBD
- Hardware targets approved: TBD
- Environment matrix approved: TBD
- Approved at UTC: TBD

The agent cannot satisfy the final human-approval acceptance criterion by itself. A human approver must record approval here before issue #7 can be considered fully approved.

## Acceptance Criteria Mapping

- Cases cover happy paths, edge cases, out-of-bounds requests, ambiguity, conflicts, stale sources, refusals, and robustness across every PRD content area: [data/evaluation/evaluation-set-v0.1-candidate.json](../../data/evaluation/evaluation-set-v0.1-candidate.json), all 20 cases.
- Retrieval and final-answer evaluation remain separate and include required facts, forbidden claims, and required or forbidden source domains: candidate dataset fields `retrieval_expectations` and `final_answer_expectations`; [tests/test_evaluation_quality_bar_contract.py](../../tests/test_evaluation_quality_bar_contract.py).
- Proposed thresholds cover retrieval, citations, unsupported claims, clarify/answer/refuse behavior, trust indicators, privacy, rollback, accessibility, and reliability: [config/evaluation-quality-bar.json](../../config/evaluation-quality-bar.json), `metrics` and `thresholds`.
- Hardware and environment targets are supported by baseline measurements: [docs/evaluation-quality-bar.md](../evaluation-quality-bar.md), "Baseline Results", "Hardware Targets", and "Environment Matrix"; [docs/progress/issue-26-runtime-probe.json](issue-26-runtime-probe.json); [docs/progress/issue-29-hybrid-retrieval-comparison.json](issue-29-hybrid-retrieval-comparison.json).
- A human approves and versions the dataset, metrics, thresholds, and environment matrix: pending human approval in this document and [docs/evaluation-quality-bar.md](../evaluation-quality-bar.md), "Human Approval Record".

## Remaining Limitations

- This work does not implement the production evaluation runner, final-answer evaluator, browser accessibility test harness, network-boundary monitor, rollback fault injection, or supported-environment CI matrix.
- The current issue #29 retrieval fixture baseline meets the proposed required-evidence Recall@3 threshold on 7 evaluable required-evidence queries, with blocked-source and forbidden-result violations still at 0. It remains baseline fixture evidence, not full release acceptance.
- Human approval is still required before this candidate becomes the release-blocking quality bar.
