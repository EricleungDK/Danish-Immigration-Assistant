# UX Guidelines — Danish Immigration RAG

## Design Philosophy

Keep the interface calm, conversation-first, and explicit about its information
boundary. The app explains retrieved official information; it does not present
itself as an authority, lawyer, or eligibility assessor.

## Visual System

Use semantic labels in addition to color. Official fact, interpretation, refusal,
and source warning sections remain distinguishable. Evidence Confidence and Fresh
Tomato Score are separate named indicators with reasons.

## Interaction Rules

- Preserve the user's question when an operation fails.
- Keep a persistent multiline composer and local history controls.
- Let users request installed-model choices from the selected loopback provider;
  preserve provider/endpoint state when discovery fails.
- Keep long active conversations inside an intentional message scroller, reveal
  the newest saved answer, and keep the composer visible without covering turns.
- On the home page (no active conversation) the composer is pinned to the bottom of
  the conversation column and the product-boundary intro scrolls above it, never
  below 8rem (128px) tall. The composer is guaranteed in view from 800px tall (the
  1280x720 fit was measured with fallback fonts in headless Linux, ~5px margin, and
  may differ with real fonts); on shorter viewports, or with a composer error below
  ~800px tall, the column itself scrolls so the composer and intro stay reachable
  (no clipped content down to 540px tall). The intro is a keyboard stop (`tabindex`
  set by `app.js`) only while it overflows.
- Inline citations open a focused evidence drawer with publisher, URL, check date,
  corpus/model identity, claim support, and trust reasons.
- Knowledge update discovery, signed download/review, and installation are three
  distinct user actions; never auto-install.

## Accessibility

All core controls are keyboard reachable, focus returns predictably from the
evidence dialog, trust states do not rely on color, and layouts remain usable at
narrow width and 200% text zoom. Respect reduced-motion preferences.

## Empty / Loading / Error States

First launch explains provider setup. Model discovery, provider testing, long
generation, and indexing work have visible status messages; provider testing
also prevents duplicate submission while it is running. Errors distinguish
provider, retrieval, validation, storage, and update failures and provide a local
corrective action without claiming success. Invalid destructive confirmations
remain inside the HTML application with an inline alert.

## Animation & Transitions

Use restrained, nonessential transitions only. Under `prefers-reduced-motion`,
remove motion while preserving status and focus behavior.

See [`docs/architecture.md`](../../architecture.md) and the browser tests in
[`tests/browser/`](../../../tests/browser/) for the executable contract.
Current completion evidence: [`../../progress/issue-51-completion.md`](../../progress/issue-51-completion.md).
Historical implementation report: [`../Reports/2026-07-14-mvp-completion-candidate.md`](../Reports/2026-07-14-mvp-completion-candidate.md).
