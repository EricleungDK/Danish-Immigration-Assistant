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
- Inline citations open a focused evidence drawer with publisher, URL, check date,
  corpus/model identity, claim support, and trust reasons.
- Knowledge update discovery, signed download/review, and installation are three
  distinct user actions; never auto-install.

## Accessibility

All core controls are keyboard reachable, focus returns predictably from the
evidence dialog, trust states do not rely on color, and layouts remain usable at
narrow width and 200% text zoom. Respect reduced-motion preferences.

## Empty / Loading / Error States

First launch explains provider setup. Long generation/indexing work has a status
message. Errors distinguish provider, retrieval, validation, storage, and update
failures and provide a local corrective action without claiming success.

## Animation & Transitions

Use restrained, nonessential transitions only. Under `prefers-reduced-motion`,
remove motion while preserving status and focus behavior.

See [`docs/architecture.md`](../../architecture.md) and the browser tests in
[`tests/browser/`](../../../tests/browser/) for the executable contract.
Completion evidence: [`../Reports/2026-07-14-mvp-completion-candidate.md`](../Reports/2026-07-14-mvp-completion-candidate.md).
