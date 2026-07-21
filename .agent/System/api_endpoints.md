# Local Web API Endpoints

All routes are served by the single loopback FastAPI process. State-changing
POST routes validate the local Host/Origin boundary. Questions and conversation
content are never placed in update requests.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Conversation-first home, setup, corpus, history, and update state. |
| POST | `/setup` | Test and persist a loopback generation-provider configuration. |
| POST | `/ask` | Retrieve eligible evidence, generate/validate an answer, and save one turn. |
| GET | `/status` | Content-free provider/model/corpus/index identity and capability status. |
| GET | `/conversations/{id}` | Reopen a local historical conversation. |
| GET | `/conversations/{id}/export.json` | Export one local conversation record. |
| GET | `/conversations/export.json` | Export all non-deleted local records. |
| POST | `/conversations/{id}/delete` | Soft-delete one local conversation. |
| POST | `/conversations/delete-all` | Confirmed soft deletion of all local records. |
| POST | `/knowledge-updates/check` | Fetch bounded, content-free release metadata only. |
| POST | `/knowledge-updates/download` | Explicitly approve one tag/asset download and verify its signed contents. |
| POST | `/knowledge-updates/install` | Separately install the reviewed staged release with atomic rollback. |
| POST | `/knowledge-updates/dismiss` | Discard available/staged update state. |
| GET | `/vendor/htmx.min.js` | Serve the installed local HTMX asset. |

## Request Format

HTML forms use URL-encoded fields. `/ask` accepts a question and optional local
conversation ID. Update download approval is bound to the persisted release ID,
GitHub asset ID, and exact archive filename; client-supplied values cannot select
a different discovered artifact.

## Response Format

Conversation routes return server-rendered HTML (or HTMX fragments). Export and
status routes return JSON. Successful mutations normally redirect with HTTP 303;
validation errors preserve the question and return a categorized local recovery
message. Provider, retrieval, validation, storage, and update failures are not
reported as successful answers or installs.

## Authentication And Constraints

The MVP has no account/authentication layer and binds to `127.0.0.1`. That makes
Host/Origin validation, loopback provider enforcement, content-free release
requests, bounded downloads, signature verification, and local file permissions
the relevant controls. Non-loopback exposure is unsupported.

Implementation: [`danish_rag/local_app.py`](../../danish_rag/local_app.py).
Completion evidence: [`../Reports/2026-07-14-mvp-completion-candidate.md`](../Reports/2026-07-14-mvp-completion-candidate.md).
