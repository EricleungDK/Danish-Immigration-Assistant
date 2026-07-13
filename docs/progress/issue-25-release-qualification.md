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
- Active corpus fixture: [data/knowledge_releases/kr-2026-07-06.1/manifest.json](../../data/knowledge_releases/kr-2026-07-06.1/manifest.json)

## TDD Record

- Red: `python3 -m unittest tests.test_issue_25_release_qualification -v` failed because `danish_rag.release_qualification` did not exist.
- Green: `danish_rag/release_qualification.py`, `config/release-qualification.json`, and `docs/release-qualification.md` were added to validate the blocked release decision and documentation contract.

## Acceptance Criteria Mapping

- Every supported environment passes setup, supported answer, refusal, evidence inspection, history persistence, deletion/export, update installation, and rollback: blocked. `windows-11-wsl2-ubuntu-x86_64` is still a verified candidate, not a release-qualified environment matrix result. Native Linux and macOS remain candidates; native Windows is not supported for the MVP candidate.
- Retrieval, citation, unsupported-claim, clarify/answer/refuse, trust-indicator, privacy, reliability, accessibility, and performance gates meet approved thresholds: blocked. The issue #29 hybrid retrieval fixture now meets the candidate required-evidence Recall@3 threshold with zero blocked-source and forbidden-result violations, and the offline release evaluation runner publishes the current gate report, but the issue #7 quality bar still requires human approval and final-answer evaluation, full release monitors, the supported-environment matrix, and approved performance thresholds remain incomplete.
- Any uncited official fact, personal eligibility conclusion, answer-path data egress, or failed atomic rollback blocks release: encoded as release-blocking zero-tolerance conditions in `config/release-qualification.json` and verified by contract tests.
- Distribution and documentation match approved runtime, model, hardware, security, and application-update decisions: documented in `docs/release-qualification.md` and checked against the release qualification contract.
- Evaluation dataset version, metrics, thresholds, results, known limitations, and active corpus requirements are published without user-question analytics: documented in `docs/release-qualification.md`; the qualification contract records `uses_production_user_questions: false`.

## Active Release Blockers

- Human approval pending for issue #7 evaluation dataset, metrics, thresholds, hardware targets, and environment matrix.
- Human confirmation pending for issue #24 usability validation.
- Performance baselines are published, but human-approved performance thresholds and full supported-environment measurements are pending.
- Offline release evaluation runner is implemented and publishes `docs/progress/release-evaluation-current.json`; release remains blocked by the missing final-answer evaluator, full network-boundary monitor, rollback fault-injection matrix, and full supported-environment critical journey matrix.
- Full critical journey matrix has not passed across every published supported environment.

## Verification

- `python3 -m unittest tests.test_issue_25_release_qualification -v`
- `.venv/bin/python -m unittest tests.test_issue_25_release_qualification -v`
- `.venv/bin/python -m unittest -v` - 113 tests passed, 1 live dense benchmark skipped by default.
- `DI_RAG_BROWSER_PORT=19731 npm run test:browser` - 12 browser tests passed. The command required unsandboxed execution because the sandbox blocks socket creation.
- `npm run typecheck` - unavailable because `package.json` does not define a `typecheck` script.

The system `python3` interpreter is missing `httpx`; the documented project virtualenv was used for full-suite verification.
