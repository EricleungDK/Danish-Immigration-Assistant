# Danish Immigration RAG — Agent Documentation

**Last updated:** 2026-09-29
**Status:** Human answer and accessibility reviews are accepted; the owner-run qualification suite records a clean Python regression pass. This documentation session's restricted sandbox run is recorded separately in the issue #51 handoff because two source-review tests could not bind local sockets. The owner-approved production and fixture retrieval checks now pass. Final release-owner approval is recorded on 2026-09-08. Qualification is approved for the documented MVP target, but publication has not been performed. See [`docs/release-qualification.md`](../release-qualification.md) and [`docs/progress/release-owner-approval-20260908.json`](../progress/release-owner-approval-20260908.json).

Start with [`CONTEXT.md`](../../CONTEXT.md), the GitHub issue named in the task,
and the authoritative contracts under [`docs/`](../). GitHub issue #1 is the
canonical product PRD, and GitHub Issues are authoritative for work items. This
directory is an agent-facing map, procedure layer, and historical archive; it
does not override those sources.

## System

- [Project architecture](System/project_architecture.md) — current components,
  boundaries, and integration points.
- [Database schema](System/database_schema.md) — local conversation and retrieval
  SQLite structures.
- [API endpoints](System/api_endpoints.md) — production local-web routes and their
  state-changing constraints.
- [UX guidelines](System/ux_guidelines.md) — conversation, evidence, trust, and
  accessibility rules.
- [Authoritative architecture](../architecture.md), [runtime baseline](../runtime-baseline.md),
  [source governance](../source-governance.md), and
  [release qualification](../release-qualification.md).

## Tasks

- [Current context](Tasks/context.md) — current implementation state, pending
  evidence, and genuine external blockers.
- [Docs index](../README.md) — all contracts and evidence.
- [Live evaluation runbook](../live-evaluation.md) — release evidence commands.
- [Task index](Tasks/README.md) — issue tracker and verification entry points.
- GitHub Issues for `EricleungDK/Danish-Immigration-Assistant` are authoritative;
  see [`issue-tracker.md`](issue-tracker.md).

## SOP

- [Development workflow](SOP/development_workflow.md) — setup, testing, evidence,
  and Git conventions.
- [Database changes](SOP/database_migrations.md) — forward-compatible local SQLite
  changes and migration tests.

## Reports

- [`Reports/`](Reports/) stores dated implementation/test handoffs.
- Durable machine-readable gate evidence lives in [`docs/progress/`](../progress/)
  so release evaluation can hash and validate it.
- [Current release qualification](../release-qualification.md) is the source for
  the active gate state; [release-owner approval](../progress/release-owner-approval-20260908.json)
  records the separate owner decision.
- [Issue #50 production knowledge release](../progress/issue-50-production-knowledge-release.md)
  records the reviewed semantic-chunk builder, v2 key reset, and live candidate installation.
- [Issue #51 live qualification evidence](../progress/issue-51-live-qualification.md)
  preserves the original production-candidate retrieval misses, live answer-validation
  failures, and private packet custody; its later acceptance is linked below.
- [Issue #51 engineering remediation](../progress/issue-51-engineering-remediation.md)
  tracks the subsequent fixes, accepted independent review, and replay evidence.
- [Issue #51 acceptance handoff](../progress/issue-51-completion.md) maps all six
  acceptance criteria to evidence and records the latest verification.

## Quick Verification

```bash
.venv/bin/python -B -m unittest discover -v
npm run test:browser
```

Live Ollama, retrieval, monitor, and strict-evaluation commands are maintained in
[`live-evaluation.md`](../live-evaluation.md), [`README.md`](../../README.md), and the release qualification docs. A fixture or unit
pass never substitutes for a required live or human gate.
