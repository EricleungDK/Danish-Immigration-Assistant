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
