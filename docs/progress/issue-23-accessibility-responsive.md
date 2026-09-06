# Issue #23 Accessibility And Responsive Review

Date: 2026-07-07

## Automated Checks

The commands and coverage below describe the prior passing automation run. UI
and update-progress code changed afterward, so its results are historical and a
current `npm run test:browser` run is required before qualification.

- `npm run test:browser`
  - Covers setup, conversation, evidence drawer, history, export, deletion, update controls, narrow reflow, status announcements, and axe checks.
  - Axe checks gate critical and serious violations on first launch, setup error, answered conversation, saved history, update review, and the evidence drawer.
- `.venv/bin/python -m unittest`
  - Covers local app routes, persistence, evidence safety, updates, deletion/export, privacy boundary, and recovery behavior.

## Design And Browser-Interaction Review

- Keyboard order starts with "Skip to conversation", then the product header, conversation, local tools, and provider setup. The conversation remains the first primary work area at desktop, 200% reflow-equivalent width, and narrow mobile width.
- Focus is visible on links, buttons, form fields, the skip target, citation buttons, and the evidence drawer close button.
- Evidence drawer focus moves to the drawer title on open, cycles within the modal controls, closes with Escape or the close button, and returns focus to the citation trigger.
- Screen-reader landmarks are named for the main application area, local tools, provider setup, runtime status, saved conversations, product boundaries, and trust indicators.
- HTMX setup and answer submissions update a polite live region with progress or completion status. Regular export, deletion, and update forms use native page navigation/download semantics, with returned pages exposing the resulting state in the page content.
- Error states use `role="alert"` and preserve the relevant draft values for setup and composer recovery.
- Evidence confidence, Fresh Tomato Score, source warnings, and evidence-bounded refusals are labeled in text. Official facts, interpretations, source warnings, and refusals also use different border patterns, not color alone.
- At 200% reflow-equivalent width and at 390px width, setup, conversation, history, export/delete, and update controls remain reachable without horizontal page scrolling.

## Residual Risk

- The prior review validated browser semantics and keyboard interaction with Playwright, but must be rerun after the current UI changes. It did not exercise an actual assistive-technology session or capture its output.
- The approved quality bar requires both current browser automation and a manual assistive-technology check. Both are `not_verified` and remain release-blocking; the historical automated results above do not substitute for either gate.

## Manual review follow-up (2026-09-06)

The owner reported that checking for knowledge updates with Windows Narrator
read the whole page without an identifiable result. The manual update-review
journey is failed pending retest; it is not covered by the earlier automated pass.

The manual check now swaps only the update panel, announces checking and the
actual result through the existing live region, preserves the question draft,
and returns focus to the check button. Empty, available, failed, and conflicting
checks receive visible feedback. Origin validation and separate download/install
approval remain enforced. Non-JavaScript submission retains a redirect with an
explicit result at the updates section.

Regression coverage includes empty/available/error responses, origin rejection,
and a browser check for the announced result, preserved draft/page, and focus.
The current browser suite passes 35 tests with one opt-in live test skipped.
The final Python suite passes 468 tests with five opt-in skips (463 executed).
Actual Narrator retesting, remaining manual journeys, and tool/browser version
recording are still required before the manual gate can pass.

### Narrator retest and focus preservation

The owner retested the first fix and still heard Narrator start at the header.
Live browser inspection confirmed no navigation, but the focused check button
was removed and replaced. The follow-up keeps the form/button and a nearby live
region mounted, updates only the release details, and avoids programmatic
refocusing or disabling the focused button. Duplicate requests are dropped while
one is running. Failed requests receive local feedback too.

Nested installation polling does not inherit the parent update response selector
or target. The existing installation-completion browser test covers that boundary.
The update-flow Python tests pass (17 tests). The full browser run passed all
35 previously passing tests, including installation polling. A new simulated
network-failure test exposed an HTMX event without a failure flag; that path now
checks the transport status too. All ten accessibility browser tests then passed.
The manual gate remains failed pending a new actual Narrator retest.

## Required Manual Gate

A human reviewer must use an actual screen reader or equivalent assistive technology in the published supported environment and record the tool/version, browser/version, date, reviewer identity, and pass/fail observations for provider setup, question submission, answer/status announcements, inline citation navigation, evidence-drawer focus/close behavior, history navigation, update review, and error recovery. Do not mark the gate passed from accessibility-tree inspection or automated axe results alone.

The evidence file consumed by the release evaluator uses schema
`manual-assistive-technology-v1`, lives under `docs/progress/`, and contains no
questions, answers, conversation IDs, or participant data beyond the reviewer ID:

```json
{
  "schema_version": "manual-assistive-technology-v1",
  "status": "passed",
  "reviewer_id": "<reviewer-id>",
  "assistive_technology": "<name and version>",
  "browser": "<name and version>",
  "tested_at_utc": "<YYYY-MM-DDTHH:MM:SSZ>",
  "journeys": [
    {"id": "provider-setup", "status": "passed"},
    {"id": "question-submission", "status": "passed"},
    {"id": "answer-status-announcements", "status": "passed"},
    {"id": "inline-citation-navigation", "status": "passed"},
    {"id": "evidence-drawer-focus-close", "status": "passed"},
    {"id": "history-navigation", "status": "passed"},
    {"id": "update-review", "status": "passed"},
    {"id": "error-recovery", "status": "passed"}
  ]
}
```

After review, `config/release-qualification.json` must bind the exact file path,
SHA-256, reviewer ID, assistive-technology identity, browser identity, and test
timestamp. The evaluator fails closed on a missing journey, mismatch, or changed
file hash.

### Owner confirmation of update-check feedback

The owner confirmed that Narrator now announces the update-check result correctly
after commit `8da58c2`. This resolves the reported header-reading defect. The
full manual gate remains in progress: available-release review/approval controls,
error recovery, and exact tool/browser identities still need confirmation.

### Download-to-review transition

The owner tested available-release controls in the isolated local fixture app
on port 8925 and reported a new full-page reading reset after downloading.
The download action now returns the update details fragment for HTMX requests
and moves keyboard focus to the signed-release review heading. Local status
reports progress and specific server failures; network failures remain retryable.
Exact artifact approval, signature verification, and separate install approval
are preserved. Non-HTMX clients retain the original redirect behavior.

A browser regression reproduced the old navigation before the fix and verifies
page preservation, review-heading focus, and the subsequent explicit install.
A separate error regression checks signature-failure feedback and retained
controls. The update-flow Python tests pass (17 tests). The manual update-review
gate remains failed pending the owner's retest; fixture review is not production
source or signing qualification.

Download follow-up verification: full browser suite 37 passed, one opt-in live
test skipped; scoped code review found no blocking issues.
