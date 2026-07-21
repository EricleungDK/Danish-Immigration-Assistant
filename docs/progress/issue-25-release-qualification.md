# Issue 25 Release Qualification

GitHub issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/25

Parent PRD issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/1

## Release Decision

Status: blocked

Decision: do-not-release

This slice packages the local application release candidate as a documented source distribution and publishes the release qualification record. It does not declare MVP release readiness because release-blocking gates remain open.

## Trace

- Machine-readable release qualification: [config/release-qualification.json](../../config/release-qualification.json)
- Human-readable release qualification and operating documentation: [docs/release-qualification.md](../release-qualification.md)
- Current release evaluation report: [docs/progress/release-evaluation-current.json](release-evaluation-current.json)
- Release qualification contract code: [danish_rag/release_qualification.py](../../danish_rag/release_qualification.py)
- Contract tests: [tests/test_issue_25_release_qualification.py](../../tests/test_issue_25_release_qualification.py)
- Runtime baseline: [docs/runtime-baseline.md](../runtime-baseline.md), [config/runtime-policy.json](../../config/runtime-policy.json)
- Evaluation quality bar: [docs/evaluation-quality-bar.md](../evaluation-quality-bar.md), [config/evaluation-quality-bar.json](../../config/evaluation-quality-bar.json)
- Source governance and corpus rules: [docs/source-governance.md](../source-governance.md)
- Production source-registry assessment: [data/source_registry/sr-2026-07-06.1.json](../../data/source_registry/sr-2026-07-06.1.json)
- Active corpus fixture: [data/knowledge_releases/kr-2026-07-06.1/manifest.json](../../data/knowledge_releases/kr-2026-07-06.1/manifest.json)
- Live final-answer report: [docs/progress/final-answer-evaluation-live.json](final-answer-evaluation-live.json)
- Live privacy, rollback, and supported-environment report: [docs/progress/release-monitors-live.json](release-monitors-live.json)

## TDD Record

- Red: `python3 -m unittest tests.test_issue_25_release_qualification -v` failed because `danish_rag.release_qualification` did not exist.
- Green: `danish_rag/release_qualification.py`, `config/release-qualification.json`, and `docs/release-qualification.md` were added to validate the blocked release decision and documentation contract.
- Refresh red: `.venv/bin/python -B -m unittest tests.test_issue_25_release_qualification -v` failed while the contract still named the live monitor gates as not implemented and treated absent numeric latency thresholds as a pending gate.
- Refresh green: the qualification contract now consumes the existing evidence states, keeps the final-answer and source-review failures closed, and treats performance as measurement completeness because no numeric latency SLA is configured.

## Acceptance Criteria Mapping

- Every published supported environment passes setup, supported answer, refusal, evidence inspection, history persistence, deletion/export, update installation, and rollback: `not_verified`. The previous monitor ran the app in process, did not restart it, and copied environment identity, so replacement real-process/browser evidence is required for `windows-11-wsl2-ubuntu-x86_64`.
- Retrieval, citation, unsupported-claim, clarify/answer/refuse, trust-indicator, privacy, reliability, accessibility, and performance gates meet approved thresholds: blocked overall. Hybrid retrieval, the live privacy/rollback monitors, performance measurement completeness, and every machine-evaluable final-answer gate pass. The environment matrix requires replacement evidence; final-answer semantics require independent adjudication; and accessibility requires both a current Playwright rerun and an actual assistive-technology check.
- Any uncited official fact, personal eligibility conclusion, answer-path data egress, or failed atomic rollback blocks release: encoded as release-blocking zero-tolerance conditions in `config/release-qualification.json` and verified by contract tests.
- Distribution and documentation match approved runtime, model, hardware, security, and application-update decisions: documented in `docs/release-qualification.md` and checked against the release qualification contract.
- Evaluation dataset version, metrics, configured thresholds, results, known limitations, and active corpus requirements are published without user-question analytics: documented in `docs/release-qualification.md`; the qualification contract records `uses_production_user_questions: false`. No numeric latency SLA is configured, so the performance gate checks measurement completeness.

## Active Release Blockers

- The live final-answer run completed 20 of 20 cases with zero errors and passed every machine-evaluable gate. Independent-human adjudication is still absent for required-fact coverage, forbidden claims, privacy prose, citation correctness, and unsupported-claim rate.
- The machine-readable source-registry assessment is blocked: the current project-authored fixtures lack curator admissions, monitoring records, archived official snapshots, named human production reviews, and a durable production signing-key custody record.
- The supported-environment matrix needs replacement real-process/browser evidence with an actual process restart and observed environment/browser identity.
- The prior automated accessibility run passed before later UI changes. A current Playwright/accessibility rerun and the required manual assistive-technology check have not been performed or recorded.
- Final production release-owner approval remains pending.

Product owner approval was already provided through the initiating GPT goal instruction on 2026-07-13 for the existing issue #7 and issue #24 decision records; it is not an active blocker and does not change implementation or test gates.

The live privacy monitor, instrumented in-memory GitHub transport exercise, six-phase rollback matrix, performance measurement-completeness gate, and all machine-evaluable final-answer gates are not active blockers. The GitHub exercise did not contact or publish a production release. The previous eight-journey environment report is diagnostic only and is an active blocker until replaced.

## Verification

- `.venv/bin/python -B -m unittest discover` — 242 tests passed; 2 expected opt-in live skips.
- Focused remediation, release-monitor, supported-environment, release-evaluation, qualification, runtime, and update modules — 91 tests passed.
- Python AST parsing and `node --check danish_rag/supported_environment_browser.mjs` passed.
- The current checked-in aggregate evaluation exits `1` in strict mode while fail-closed release blockers remain. The checked-in report is the equivalent non-strict diagnostic output; its own `generated_at_utc` field records the run time without creating a self-referential documentation hash.
- A current live monitor attempt could not inspect `embeddinggemma` because Ollama was unavailable in the sandbox. Elevated Playwright execution was rejected before running by the platform usage limit, so browser and replacement supported-environment evidence remain unverified.
