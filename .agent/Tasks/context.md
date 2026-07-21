# Current Project Context

**Last updated:** 2026-07-14

## Current Project State

The production path is implemented for Ollama `gemma4:12b`, local
`embeddinggemma`, verified signed knowledge releases, SQLite FTS5 plus dense RRF
retrieval, evidence-bounded structured answers, local conversations, citations,
trust indicators, staged GitHub update approval, and atomic rollback.

Machine verification is consolidated under `docs/progress/`. Release is
still blocked rather than declared complete because the production source registry
truthfully records fixture summaries rather than human-reviewed official snapshots,
exact final-answer executions still require independent human adjudication, the
supported-environment matrix needs replacement real-process/browser evidence, and
the manual assistive-technology gate has not been run.

## Active Tasks

- Rerun the elevated Playwright suite and live Ollama supported-environment
  monitor, then regenerate strict monitor/final workflow evidence. The current
  platform usage limit prevented that run; the Python suite is current and clean.
- Obtain named curator/monitor/source-review evidence and rebuild the production
  signed corpus from official snapshots.
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

## Known Issues

- The active source registry has zero production-qualified human-reviewed sources.
- Independent human answer adjudication, manual assistive-technology evidence,
  and production release-owner approval are not recorded.
- The prior environment monitor did not qualify a restarted real process/browser.
- macOS and native Linux remain unpublished environment candidates.

## Active Delegations

No durable delegation state belongs in this file; use the active Codex thread for
ephemeral ownership. Completed implementation evidence is recorded in dated reports
and `docs/progress/`.
