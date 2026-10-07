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

- **Scope (D1, review round 1).** Only releases identical (ID and full signed manifest)
  to one shipped in `data/knowledge_releases/*` are rebased. Any other release keeps
  wall-clock freshness (real due/block-after dates fire) and shows no snapshot label.
  No signed-manifest change.
- **Clock.** `danish_rag/snapshot_clock.py` swaps `source_freshness.datetime` once for a
  subclass whose `now()` returns the snapshot time while a `snapshot_clock(state)` scope
  is active (`ContextVar`: per request, never process-wide) and otherwise defers to the
  previous `datetime` (real, or the #64 pin). It mirrors `datetime.now()` (naive local
  time without tz); the wrapper marker is checked with `vars()` so a subclass is
  re-wrapped. Only `source_freshness` is wrapped (the only freshness clock on the app
  path); `evidence_integrity` / `grounded_flexibility_evaluation` keep the wall clock, so
  evaluation CLIs are not rebased. Install-time indexing needs no scope (eligibility is
  judged at retrieval time; a test pins that).
- **State from the verified release only (D2, revised).** `local_app._SnapshotClockMiddleware`
  gives each request an empty `SnapshotState` (wall clock) and does no release I/O; the
  earlier request-start read and stat-keyed cache were removed (the extra verification
  slowed requests and could not see corpus/index/trust-root changes). The state is filled
  only by `ensure_release()`'s verified result and, in `/ask`, by the retriever's manifest
  (the data judged is the data loaded, so label and stored turn follow it if a concurrent
  install changed the release). If either load fails the state becomes "unavailable" for
  the rest of the request (sticky): wall clock, warning logged, banner "Snapshot date
  unavailable", `/status` `snapshot_status: "unavailable"`. Bundled identity is the
  sha256 of canonical JSON (type-strict: 1, 1.0 and true differ; NaN refused), memoized per
  bundled release id and invalidated when the shipped manifest file changes.
- **Pin interaction.** Inside app requests the snapshot clock wins over the #64 pin;
  outside requests (evidence tests, CLIs) the pin still governs. Both are fixed times
  before the due dates, so the suites stay deterministic; tests move the pin to
  2027-01-01 to prove requests ignore it. Fixtures, signatures, trust roots and the
  fingerprinted files are untouched.
- **Label.** `home.html` banner (three states: snapshot / unavailable / none). Each turn
  stores `knowledge_snapshot` {release_id, snapshot_date, kept_current: false} in its
  answer JSON (so exports carry it per turn); the template note beside Fresh Tomato
  Scores renders from the turn's own metadata, so older turns and non-bundled releases
  show none. `/status` has top-level `snapshot_status` (`snapshot`/`unavailable`/`none`) and `knowledge_snapshot`. Fresh Tomato reason text comes
  from fingerprinted `answer_pipeline.py` ("current and healthy"); the note scopes it.
  Layout: `app.js` sets `--chrome-height` to the measured top bar + banner height
  (replaces the fixed 4.25rem; banner wraps at narrow widths).
- **Fresh install.** `knowledge_release.newest_bundled_release_dir(catalog, trust_root_path=,
  application_version=)` returns the newest bundled release that passes
  `verify_knowledge_release` (falls back to older verified ones, then
  `BUNDLED_MINIMAL_RELEASE`; never raises on a missing catalogue). Skips only verification
  failures (`ValueError`/`OSError`) and logs a WARNING naming the release and reason;
  unexpected errors surface.
  `ensure_minimal_knowledge_release(release_dir=)` accepts a callable resolved only when a
  fresh install is needed. The production `local_app.app` and the documented launch
  command (`docs/release-qualification.md`) pass `initial_release_dir=
  newest_bundled_release_dir`, so a new install gets `kr-2026-09-05.1`. `create_app()`
  called directly (tests, browser fixture server) keeps the `kr-2026-07-06.1` fixture
  default, because many tests depend on that fixture's content;
  `BUNDLED_MINIMAL_RELEASE` is unchanged (evaluation modules use it).
- **Known limits.** A data dir indexed by an older build keeps its index; the clock only
  changes evaluation. Existing installs of `kr-2026-07-06.1` keep working (snapshot
  2026-07-06) and can still update to the September release.
- Tests: `tests/test_snapshot_mode.py` (wall clock forced to 2027-01-01: both bundled
  releases still cite; label states, per-turn notes, race, gate, fresh install, tamper
  refusal) and Playwright label/layout tests (360x740, 390x844, 681-1280). Also run with
  the pin at 2027-01-01 in the browser server and with every other `danish_rag`
  module's `datetime` forced to 2027-01-01: green.

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
- 2026-10-01 GH-59: home composer no longer clipped on desktop or 681-1080px.
  Causes: home `h1` sized by viewport width wrapped to ~6 lines in the narrow centre
  column, and `.empty-state` (min-height auto) could not shrink, so `.composer` fell
  outside the `overflow: hidden` `.conversation`. Fix (`app.css`, `app.js`): composer
  stays pinned; `.empty-state` shrinks/scrolls above it but keeps `min-height: 8rem`
  (128px: h2 + start of boundary text); if column cannot fit both, `.conversation`
  (now `overflow-y: auto`) scrolls, so nothing is clipped. Home `h1` is column-sized
  (`clamp(2.2rem, min(3.4vw, 7vh), 3.6rem)`) for all widths >680px (continuous at
  1080/1081); home padding/head margin shrink with height. `.empty-state` gets
  `tabindex="0"` from `app.js` only while it overflows (ResizeObserver on the intro
  and its child blocks, re-run after htmx swap; a focused intro keeps tabindex until
  blur); none at <=680px or when it fits. Measured floor
  (headless Chromium on Linux, where Georgia/Aptos/Segoe UI fall back to DejaVu, so
  real fonts on Windows may differ): plain home keeps the composer pinned with intro
  >=128px from 800px tall at 1024-1920px wide (tested widths; hard test guarantee; 1280x720 fits by only ~5px, intro
  133px, so it is tested as scroll-allowed); shorter (1366x650, 1280x600, 960x540) or
  with a composer error below ~800px tall the column scrolls to reach the composer.
  Browser tests: pinned 1024x800..1920x1200, short 1280x720..700x540, no-JS real 422
  error, heading continuity, Tab-stop/focus-retention/content-growth behaviour.
  Also fixed flaky eval-016 (guard waited on `#knowledge-updates` instead of
  `#knowledge-update-content`, so the page-load check poll could land in the
  recorded window; added a deterministic variant: after the answer, a simulated
  metadata check keeps polling until it completes on its own clock).
- 2026-10-01 (#60): capability/runtime probe now sends `think: false` like the answer
  path. Without it cold `gemma4:12b` on Ollama 0.34.0 burned the token budget on
  thinking (empty content, `done_reason=length`, ~86 s) and failed setup. Live: cold
  3/3 pass (~6-7 s), warm ~1 s. No threshold/schema/timeout changed.

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
