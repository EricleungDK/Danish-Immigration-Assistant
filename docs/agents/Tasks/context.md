# Current Project Context

**Last updated:** 2026-10-07

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

## README demo recording — 2026-09-30

`recording/` (portfolio GH-63) records the README demo reproducibly:
`.venv/bin/python -m recording.record_demo --revision <commit>` checks the
commit out in a temporary worktree, runs it with fresh XDG dirs, signed
`kr-2026-09-05.1` and real local `gemma4:12b`, captures 1920x1200 @2x
(CDP screencast, drawn cursor following real input), cuts and labels the model
wait, and writes `docs/assets/demo.{gif,mp4,webm}`, `demo-poster.png`,
`demo-recording.json`. Needs `recording/requirements.txt` (Pillow) and ffmpeg.
Current assets: commit `5d6e02b`, Ollama 0.34.0, 18 s wait → 3 s.
Findings (not fixed here): below ~1200px tall the desktop home clips the
composer (`.conversation` overflow hidden, not scrollable); the setup probe
omits `think: false`, so a cold `gemma4:12b` on Ollama 0.34 can return empty
structured output (harness preloads the model and retries).

## Fixture freshness clock pinned — 2026-10-07 (#64)

Both suites expired on 2026-10-06 12:00 UTC: the bundled fixture release
`kr-2026-07-06.1` gives its sources `next_review_due_utc` 2026-10-06T12:00:00Z and
freshness used the wall clock, so answers lost citations (~42 Python, 10 browser
failures on unmodified main). `tests/__init__.py` pins freshness evaluation to
2026-10-01T00:00:00Z via `tests/fixture_clock.py`, which swaps the module-level
`datetime` of the three modules that supply freshness evaluation times
(`source_freshness`, `evidence_integrity.utc_now_seconds`,
`grounded_flexibility_evaluation._utc_now`); a test asserts the pin precedes every
fixture release's review due date. The browser fixture server gets the pin by
importing the `tests` package. Checked: with every other `danish_rag` module's clock
set to 2027-01-01 the full suite still passes. Production code is unchanged on
purpose: its hash is bound by approved release evidence (editing it fails "approved retrieval
implementation changed"). Fixtures and signatures unchanged.
Not fixed by #64 (owner decision, resolved by snapshot mode below): on a fresh install
the app installed the bundled `kr-2026-07-06.1`, whose sources have been overdue since
2026-10-06, so new users got answers without citations; `kr-2026-09-05.1` sources fall
due 2026-10-26T20:55:12Z.

## Snapshot mode — 2026-10-07 (#67)

Owner decision: the knowledge release is a demo snapshot, not kept current (no
re-reviews, CI signing, or further GitHub releases). Built on #64 (PR #65), branch
`feat/snapshot-mode`.

- **Clock.** `danish_rag/snapshot_clock.py`: the running app evaluates source freshness
  at the verified active release's manifest `created_at_utc`. It swaps
  `source_freshness.datetime` once for a subclass whose `now()` returns the snapshot time
  while a `snapshot_clock` scope is active (a `ContextVar`: per request, never
  process-wide) and otherwise defers to the previous `datetime` (real, or the #64 pin).
  `local_app._SnapshotClockMiddleware` opens the scope for every HTTP request; the time
  is resolved lazily, once per request, from `load_active_release` (signature-verified),
  so install and rollback apply on the next request with no stored state. Only
  `source_freshness` is wrapped: it is the only freshness clock on the app path (answer
  path, retrieval eligibility, trust indicators). `evidence_integrity` and
  `grounded_flexibility_evaluation` feed evidence/qualification code only and keep the
  wall clock, so evaluation CLIs are not rebased. Install-time indexing needs no scope:
  eligibility is judged at retrieval time (a test pins that).
- **Pin interaction.** Inside app requests the snapshot clock wins over the #64 pin;
  outside requests (evidence tests, CLIs) the pin still governs. Both are fixed times
  before the due dates, so the suites stay deterministic; tests move the pin to
  2027-01-01 to prove requests ignore it. Fixtures, signatures, trust roots and the
  fingerprinted files are untouched.
- **Label.** `home.html` banner and a template-level note beside every Fresh Tomato
  Score (answer, evidence drawer) state the snapshot date, "Not kept current" and "not
  legal advice". Fresh Tomato reason text comes from fingerprinted `answer_pipeline.py`
  ("current and healthy"); the note scopes it to the snapshot.
- **Fresh install.** `knowledge_release.newest_bundled_release_dir()` picks the newest
  bundled signed release; `ensure_minimal_knowledge_release(release_dir=...)` installs it
  with unchanged verification. The production `local_app.app` (`python -m
  danish_rag.local_app`) passes it, so a new install gets `kr-2026-09-05.1`.
  `create_app()` called directly (tests, browser fixture server) keeps the
  `kr-2026-07-06.1` fixture default via `initial_release_dir`, because many tests depend
  on that fixture's content; `BUNDLED_MINIMAL_RELEASE` is unchanged (evaluation modules
  use it).
- **Known limit.** A data dir indexed by an older build keeps its index; the clock only
  changes evaluation. Existing installs of `kr-2026-07-06.1` keep working (snapshot
  2026-07-06) and can still update to the September release.
- Tests: `tests/test_snapshot_mode.py` (wall clock forced to 2027-01-01: both bundled
  releases still cite; label, wording, fresh install, tamper refusal) and a Playwright
  label test. Also run with the pin at 2027-01-01 in the browser server and with every
  other `danish_rag` module's `datetime` forced to 2027-01-01: green.

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
