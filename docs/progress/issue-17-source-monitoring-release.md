# Issue 17 Source Monitoring And Reviewed Knowledge Release

GitHub issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/17

Parent PRD issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/1

## Trace

- Approved governance baseline: [docs/source-governance.md](../source-governance.md)
- Release installer and verifier: [danish_rag/knowledge_release.py](../../danish_rag/knowledge_release.py)
- Maintainer workflow tooling: [danish_rag/source_maintenance.py](../../danish_rag/source_maintenance.py)
- Regression tests: [tests/test_issue_17_source_monitoring_release.py](../../tests/test_issue_17_source_monitoring_release.py)

## Implemented Behavior

- Automated source checks capture HTTP status, redirects, selected HTTP metadata, extraction outcome, source-content hash, normalized-document hash, visible dates, check timestamp, source health, and release-gate policy.
- Changed source content is assigned `changed-unreviewed` and remains blocked from release assembly until `approve_source_check` records human reviewer evidence.
- Redirected, extraction-failed, broken, fetch-failed, unapproved, and blocked states are not release-eligible. `overdue-policy-usable` remains release-eligible according to the approved policy.
- Release assembly writes normalized `corpus/documents.json` plus a manifest containing source provenance, approval state, hashes, schema versions, minimum application version, artifact hash, and integrity evidence.
- Release verification accepts the bundled reviewed fixture and rejects missing reviewer approval, missing provenance, incompatible minimum application versions, and missing integrity evidence.

## Verification

- `.venv/bin/python -m unittest`
- `DI_RAG_BROWSER_PORT=8918 npm run test:browser`
