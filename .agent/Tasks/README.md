# Task And Evidence Index

## Issue Tracker

GitHub Issues for `EricleungDK/Danish-Immigration-Assistant` are authoritative.
Read [`docs/agents/issue-tracker.md`](../../docs/agents/issue-tracker.md) and the
issue body/comments before implementation. The MVP PRD is issue #1.

## Current Work

See [`context.md`](context.md) for implementation state and truthful blockers.
Use [`config/release-qualification.json`](../../config/release-qualification.json)
and [`docs/progress/release-evaluation-current.json`](../../docs/progress/release-evaluation-current.json)
for machine-readable gate state rather than inferring completion from issue labels.
The latest implementation and verification roll-up is
[`../Reports/2026-07-14-mvp-completion-candidate.md`](../Reports/2026-07-14-mvp-completion-candidate.md).

## Verification Entry Points

- Full Python suite: `.venv/bin/python -B -m unittest discover -v`
- Browser/accessibility: `npm run test:browser`
- Live runtime: `.venv/bin/python -B -m danish_rag.runtime_probe ...`
- Retrieval: `.venv/bin/python -B -m danish_rag.retrieval_benchmark ...`
- Release monitors/evaluation: commands in
  [`docs/release-qualification.md`](../../docs/release-qualification.md)

Do not close issue #1 while any release-critical machine or human gate is failed,
blocked, stale, or not evaluable.
