# Local Web API Endpoints

All routes are served by the single loopback FastAPI process. State-changing
POST routes validate the local Host/Origin boundary. Questions and conversation
content are never placed in update requests.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Conversation-first home, setup, corpus, history, and update state. |
| POST | `/setup/models` | Discover compatible generation-model choices from the selected loopback provider and return an HTML setup fragment. |
| POST | `/setup` | Test and persist a loopback generation-provider configuration. |
| POST | `/ask` | Retrieve eligible evidence, generate/validate an answer, and save one turn. |
| GET | `/status` | Content-free provider/model/corpus/index identity and capability status. |
| GET | `/conversations/{conversation_id}` | Reopen a local historical conversation. |
| GET | `/conversations/{conversation_id}/export.json` | Export one local conversation record. |
| GET | `/conversations/export.json` | Export all non-deleted local records. |
| POST | `/conversations/{conversation_id}/delete` | Soft-delete one local conversation. |
| POST | `/conversations/delete-all` | Confirmed soft deletion of all local records. |
| POST | `/knowledge-updates/check` | Fetch bounded, content-free release metadata only. |
| POST | `/knowledge-updates/automatic-check` | Start a throttled background check for bounded, content-free release metadata. |
| GET | `/knowledge-updates/automatic-check-status` | Return the local status fragment for the automatic metadata check. |
| POST | `/knowledge-updates/download` | Explicitly approve one tag/asset download and verify its signed contents. |
| POST | `/knowledge-updates/install` | Separately install the reviewed staged release with atomic rollback. |
| GET | `/knowledge-updates/install-status` | Return local installation progress and terminal status. |
| POST | `/knowledge-updates/dismiss` | Discard available/staged update state. |
| GET | `/vendor/htmx.min.js` | Serve the installed local HTMX asset. |

## Request Format

HTML forms use URL-encoded fields. `/ask` accepts a question and optional local
conversation ID. Update download approval is bound to the persisted release ID,
GitHub asset ID, and exact archive filename; client-supplied values cannot select
a different discovered artifact. Model discovery submits the provider and endpoint
in a same-origin POST body; provider endpoint data is not placed in the URL.

## Response Format

Conversation routes return server-rendered HTML (or HTMX fragments). `/ask`
responses also refresh the Local History projection out of band after a saved
turn. Automatic-check and installation-status routes return local status
fragments; the installation itself runs in a background worker. Export and
status routes return JSON. Successful mutations normally
redirect with HTTP 303; validation errors preserve the relevant form state and
return a categorized local recovery message. Invalid bulk-deletion confirmation
returns the HTML application with an inline alert rather than FastAPI JSON.
Provider, retrieval, validation, storage, and update failures are not reported
as successful answers or installs.

## Authentication And Constraints

The MVP has no account/authentication layer and binds to `127.0.0.1`. That makes
Host/Origin validation, loopback provider enforcement, content-free release
requests, bounded downloads, signature verification, and local file permissions
the relevant controls. Non-loopback exposure is unsupported.

Implementation: [`danish_rag/local_app.py`](../../../danish_rag/local_app.py).
Current completion evidence: [`../../progress/issue-51-completion.md`](../../progress/issue-51-completion.md).
Historical implementation report: [`../Reports/2026-07-14-mvp-completion-candidate.md`](../Reports/2026-07-14-mvp-completion-candidate.md).
