# Current Project Context

**Last updated:** 2026-09-05

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
owner's Ubuntu account; an off-device backup is pending before publication.
The final issue-51 candidate capture completed all 20 surfaces with zero
execution errors and no failed machine gates. Exact final-answer executions
still require independent human adjudication; certificate equivalence is an
explicit partial answer whose required-fact coverage remains for human review.
Fresh strict real-process/browser evidence now passes for the supported
environment; the manual assistive-technology gate has not been run.

## Active Tasks

- Complete independent human review of the exact packet named in
  `docs/progress/issue-51-engineering-remediation.md`, then replay it with the
  accepted private adjudications and all six machine workflow records. Retrieval,
  live monitors, workflows, and final answer machine gates now pass. Earlier
  failed captures remain historical evidence; canonical packet G is unchanged.
- Back up the v2 private signing key off-device before public publication; see
  `docs/progress/issue-50-signing-key-reset.json` for its custody path. The reviewed
  issue-50 candidate is now signed and locally installed.
- Obtain independent human final-answer adjudication and final release-owner
  approval.
- Run and record the required manual assistive-technology check.

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

## Known Issues

- The reviewed candidate is locally installed; public publication and independent
  off-device signing-key backup are not yet recorded.
- Independent human answer adjudication, manual assistive-technology evidence,
  and production release-owner approval are not recorded.
- Current strict real-process/browser evidence is recorded in
  `docs/progress/issue-51-remediation-monitors.json`; historical failed reports
  remain preserved.
- macOS and native Linux remain unpublished environment candidates.

## Active Delegations

No durable delegation state belongs in this file; use the active Codex thread for
ephemeral ownership. Completed implementation evidence is recorded in dated reports
and `docs/progress/`.
