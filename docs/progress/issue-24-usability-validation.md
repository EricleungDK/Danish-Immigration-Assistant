# Issue #24 Usability Validation Packet

Date: 2026-07-07

GitHub issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/24

Parent PRD issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/1

## Purpose

Validate whether people using the production local app can:

- find material evidence without being overloaded;
- distinguish Evidence Confidence from Fresh Tomato Score;
- understand supported answers, partial answers, and evidence-bounded refusals;
- recognize knowledge update review, successful installation, and rollback.

The unresolved prototype questions came from
[visualization/danish-rag-prototype-notes.md](../../visualization/danish-rag-prototype-notes.md):
evidence discoverability, score distinction, usefulness of partial refusal, and rollback clarity.

## Production Evidence Under Test

- Evidence drawer, provenance, and trust indicators: `tests/test_issue_11_evidence_inspection.py`
- Partial answers, source warnings, and refusals: `tests/test_issue_13_evidence_safety.py`
- Knowledge release review, install, and rollback: `tests/test_issue_18_knowledge_updates.py`, `tests/test_issue_19_atomic_install.py`
- Keyboard and narrow-screen accessibility: `tests/browser/accessibility.spec.js`
- Update outcome messaging added for this issue: `tests/test_issue_24_usability_validation.py`

## Participant And Privacy Rules

- Use synthetic or deliberately contributed task prompts only.
- Do not ask participants for CPR numbers, exact immigration status, employer details, residence dates, case numbers, family details, salary, health, criminal history, or other unrelated personal immigration facts.
- If a participant volunteers personal details, do not transcribe them. Record only the interaction problem in product-neutral language.
- Record comprehension, navigation path, assistive-technology or viewport context, and task outcome. Do not record immigration advice or participant eligibility judgments.

## Scenarios

### S1 Evidence Discovery

Start from a supported answer about Danish language requirements. Ask the participant to verify one official fact by opening the evidence drawer and identifying the page title, publisher, official URL, checked date, and corpus.

Pass signal: participant can find the evidence without leaving the conversation and can return to the answer.

### S2 Score Distinction

Show an answer with High Evidence Confidence and a separate Fresh Tomato Score. Ask what each score means and whether one can substitute for the other.

Pass signal: participant explains that Evidence Confidence is about source support and citation coverage, while Fresh Tomato Score is about source recency and health.

### S3 Partial Answer

Use a prompt that combines a supported language-requirement question with an unsupported personal conclusion, such as: "I passed PD2 and have worked for years. Do I qualify for permanent residence?"

Pass signal: participant recognizes that the app answered the supported official information but declined the personal eligibility decision.

### S4 Personal Eligibility Refusal

Ask the participant to inspect an evidence-bounded refusal and say what next safe action the product implies.

Pass signal: participant understands that the app is not an authority or lawyer and that official verification is needed for a personal case.

### S5 Update Review

Ask the participant to check for a knowledge update, review release identity, compatibility, reviewed source changes, and expected local indexing work, then decide whether installation has started.

Pass signal: participant recognizes that update discovery and review do not install the release until explicit approval.

### S6 Update Success

After explicit approval, show the post-install Corpus panel.

Pass signal: participant can name the active corpus from the visible success message.

### S7 Update Rollback

Show a failed update state produced by a simulated install failure.

Pass signal: participant recognizes that rollback occurred and that the previous active corpus/index pair remains active.

### S8 Keyboard And Narrow Screen

Repeat S1, S3, and S5 with keyboard-only navigation. Repeat S1 and S5 at a narrow viewport around 390 px wide.

Pass signal: participant can complete the tasks without pointer input, horizontal page scrolling, or losing access to evidence/update controls.

## Findings Log

| ID | Source | Severity | Finding | Recommendation | Status |
| --- | --- | --- | --- | --- | --- |
| F1 | Agent pre-validation review | Medium | Update installation success and failure were observable through active corpus state or generic errors, but the page did not explicitly name install success or rollback in the Corpus panel. | Add user-visible update outcome messages that name the active corpus after success and rollback. | Completed in issue #24 implementation. |

Human-session findings should be appended here with severity `Blocker`, `High`, `Medium`, or `Low`, a concrete remediation, and a status of `Completed`, `Deferred`, or `Accepted`.

## Remediation Record

- Completed: Added explicit `Knowledge update installed` and `Knowledge update rolled back` messages in the production Corpus panel.
- Completed: Route-level tests verify the visible active corpus after install success and rollback.
- Pending human validation: No human-observed comprehension failures have been recorded yet.
- Deferred: None as of 2026-07-07.

## Human Confirmation Record

Status: pending

A human reviewer must complete this section before issue #24 is release-complete:

- Reviewer:
- Session date:
- Participant count:
- Keyboard and narrow-screen coverage confirmed:
- Required remediations completed or explicitly deferred:
- MVP release comprehension questions answered sufficiently:
- Notes:

## Verification

- `.venv/bin/python -m unittest tests.test_issue_24_usability_validation -v`
- `.venv/bin/python -m unittest`
- `DI_RAG_BROWSER_PORT=8924 npm run test:browser`
- `npm run typecheck` is unavailable because `package.json` does not define a `typecheck` script.
