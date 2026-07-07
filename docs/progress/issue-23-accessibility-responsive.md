# Issue #23 Accessibility And Responsive Review

Date: 2026-07-07

## Automated Checks

- `npm run test:browser`
  - Covers setup, conversation, evidence drawer, history, export, deletion, update controls, narrow reflow, status announcements, and axe checks.
  - Axe checks gate critical and serious violations on first launch, setup error, answered conversation, saved history, update review, and the evidence drawer.
- `.venv/bin/python -m unittest`
  - Covers local app routes, persistence, evidence safety, updates, deletion/export, privacy boundary, and recovery behavior.

## Manual Accessibility Checks

- Keyboard order starts with "Skip to conversation", then the product header, conversation, local tools, and provider setup. The conversation remains the first primary work area at desktop, 200% reflow-equivalent width, and narrow mobile width.
- Focus is visible on links, buttons, form fields, the skip target, citation buttons, and the evidence drawer close button.
- Evidence drawer focus moves to the drawer title on open, cycles within the modal controls, closes with Escape or the close button, and returns focus to the citation trigger.
- Screen-reader landmarks are named for the main application area, local tools, provider setup, runtime status, saved conversations, product boundaries, and trust indicators.
- HTMX setup and answer submissions update a polite live region with progress or completion status. Regular export, deletion, and update forms use native page navigation/download semantics, with returned pages exposing the resulting state in the page content.
- Error states use `role="alert"` and preserve the relevant draft values for setup and composer recovery.
- Evidence confidence, Fresh Tomato Score, source warnings, and evidence-bounded refusals are labeled in text. Official facts, interpretations, source warnings, and refusals also use different border patterns, not color alone.
- At 200% reflow-equivalent width and at 390px width, setup, conversation, history, export/delete, and update controls remain reachable without horizontal page scrolling.

## Residual Risk

- This review validates browser semantics and keyboard interaction with Playwright. It does not capture an actual assistive-technology audio transcript.
