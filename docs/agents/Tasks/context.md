# Current Project Context

**Last updated:** 2026-10-01

## Current Project State

The production path is implemented for Ollama `gemma4:12b`, local
`embeddinggemma`, verified signed knowledge releases, SQLite FTS5 plus dense RRF
retrieval, evidence-bounded structured answers, local conversations, citations,
trust indicators, staged GitHub update approval, and atomic rollback.

Machine verification is consolidated under `docs/progress/`. The five completed
official-source reviews now have a machine-tested production-registry and
semantic-chunk release builder. The signing key has been reset to v2, and
`kr-2026-09-05.1` is signed, live-qualified with zero retrieval violations, and
installed locally with 52 chunks. The private key is held outside Git in the
owner's Ubuntu account; an encrypted Google Drive backup is owner-confirmed,
with a matching successful download/recovery receipt on 2026-09-06.
The final issue-51 candidate capture completed all 20 surfaces with zero
execution errors and no failed machine gates. Independent human review is
accepted for all ten answer cases. Exact candidate replay passes every evaluation gate: 59/60 required facts, 47/47 supported citation
relationships, and zero unsupported claims. The failed fact judgment remains
recorded within the approved 95% coverage threshold.
Fresh strict real-process/browser evidence now passes for the supported
environment; all eight manual assistive-technology journeys passed.

## Active Tasks

- Final release-owner approval is recorded in `docs/progress/release-owner-approval-20260908.json`; publication is not performed. Human answer adjudication is complete;
  see `docs/progress/issue-51-reviewed-candidate-replay.json`.
- Production and fixture retrieval qualification now pass under the owner-approved 2026-09-08 plan; manual assistive-technology evidence is complete.

## Recent Implementations

- Real Ollama generation and embedding identity contracts.
- Hybrid SQLite FTS5/dense retrieval with RRF `k=60` and eligibility filtering.
- Claim-to-citation validation, dynamic freshness, and immutable provenance.
- Ed25519 trust root and signed release verification.
- Bounded GitHub release discovery, explicit download/review/install, safe archive
  extraction, and rollback monitoring.
- Live final-answer and release monitor harnesses with fail-closed evidence binding.
- Keyboard, reduced-motion, narrow-screen, 200% zoom, and live-Ollama browser gates.
- Completed-review-to-production-registry/release authoring with semantic chunks,
  multilingual source retrieval, exact extraction provenance, isolated
  pre-activation retrieval qualification, and candidate rollback verification.
- 2026-10-01 GH-59: desktop (>1080px) home composer no longer clipped. Cause: home
  `h1` sized by viewport width wrapped to ~6 lines in the narrow centre column, and
  `.empty-state` (min-height auto) could not shrink, so `.composer` fell outside the
  `overflow: hidden` `.conversation`. Fix in `app.css`: `.empty-state` is now a
  shrinkable scroller (`min-height: 0; flex: 1 1 auto; overflow-y: auto`, focusable
  via `tabindex="0"` with focus ring), and home `h1` is column-sized above 1080px.
  Browser tests cover 1280x720, 1280x800, 1440x900, 1600x1000. Below ~650px desktop
  height the empty-state shrinks to zero height (composer still wins).

## Known Issues

- The reviewed candidate is locally installed; public publication is not recorded.
  Signing-key backup and recovery verification are recorded in
  `docs/progress/issue-50-signing-key-reset.json`.
- Manual assistive-technology evidence is recorded in `docs/progress/issue-23-manual-assistive-technology.json`. Production release-owner approval is recorded for the reviewed candidate.
- The owner-approved production benchmark passes 15/15 critical cases. All nine original fixture cases remain mandatory, with 7/7 eligible required-evidence hits and zero safety violations. The historical 2/7 candidate/fixture diagnostic is preserved. Approval and live evidence are in `docs/progress/issue-51-production-retrieval-approval.json` and `issue-51-approved-retrieval.json`. Final release approval is not inferred from benchmark approval.
- Current strict real-process/browser evidence is recorded in
  `docs/progress/issue-51-remediation-monitors.json`; historical failed reports
  remain preserved.
- macOS and native Linux remain unpublished environment candidates.

## Active Delegations

No durable delegation state belongs in this file; use the active Codex thread for
ephemeral ownership. Completed implementation evidence is recorded in dated reports
and `docs/progress/`.
