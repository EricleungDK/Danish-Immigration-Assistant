# Issue 8 Local Application Setup

GitHub issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/8

Parent PRD issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/1

Blocked by:

- https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/2

## Trace

- Local app entry point: [danish_rag/local_app.py](../../danish_rag/local_app.py)
- Provider setup and persistence: [danish_rag/provider_setup.py](../../danish_rag/provider_setup.py)
- Template and CSS: [danish_rag/web/templates/home.html](../../danish_rag/web/templates/home.html), [danish_rag/web/static/app.css](../../danish_rag/web/static/app.css)
- Browser-facing tests: [tests/test_local_app_browser.py](../../tests/test_local_app_browser.py)
- Browser-level tests: [tests/browser/local_app.spec.js](../../tests/browser/local_app.spec.js), [playwright.config.js](../../playwright.config.js)
- Runtime policy: [config/runtime-policy.json](../../config/runtime-policy.json)

## TDD Record

- Red: `.venv/bin/python -m unittest tests.test_local_app_browser -v` failed because `danish_rag.local_app` did not exist.
- Green pass 1: added the FastAPI app, provider setup service, template, CSS, and dependency file.
- Green pass 2: moved the server-rendered HTTP tests from Starlette's synchronous `TestClient` to `httpx.ASGITransport` after the local TestClient stack hung on POST requests.
- Review fix: added HTMX-targeted setup-panel swaps, exact loopback Origin validation, and Playwright browser tests for the setup journey.

## Acceptance Criteria Mapping

- A new installation opens a conversation-first shell with the product boundary, local-only answer path, and a reachable multiline composer: `GET /`.
- The setup form offers Ollama and an OpenAI-compatible local server, so the user is not required to use Ollama.
- `POST /setup` refuses to save configuration until the selected provider and model pass the capability test.
- Failed capability tests return to the setup form, preserve provider endpoint and model fields, and display the actionable failure message.
- Successful setup is written atomically to the per-user provider configuration file, is loaded on restart, and shows the active provider/model/version without credential fields.
- State-changing browser requests validate loopback Host and Origin.
- Browser-level Playwright tests cover first launch, failed setup preservation, successful setup, reload persistence, and loaded HTMX behavior.

## Remaining Limitations

- This slice does not implement retrieval, answer generation, citations, conversation records, evidence drawers, exports, deletion, or knowledge-release installation.
- The OpenAI-compatible provider adapter verifies a local `/v1/models` and `/v1/chat/completions` contract, but broader provider compatibility remains a later provider-adapter decision.
- Automated accessibility scanning is not part of this slice. The browser tests currently cover the setup journey and visible semantics, not a full WCAG harness.
