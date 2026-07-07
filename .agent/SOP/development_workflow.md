# Development Workflow - Danish Immigration RAG

**Last Updated**: 2026-07-07

This SOP is the default path for local development in this repository. It keeps work tied to the GitHub issue tracker, preserves the local-only product boundary, and makes completion claims traceable to tests or documented evidence.

## Before Starting

1. Read the project context:
   - `AGENTS.md`
   - `CONTEXT.md`
   - `docs/architecture.md`
   - relevant progress docs in `docs/progress/`
2. If the task is issue-driven, fetch the issue and comments:

   ```bash
   gh issue view <number> --comments
   ```

3. Confirm the issue is actionable. Use the triage labels from `docs/agents/triage-labels.md`:
   - `needs-triage` for unreviewed work
   - `needs-info` when a human answer is required
   - `ready-for-agent` when an agent can implement it
   - `ready-for-human` when a human must decide or implement
   - `wontfix` when it will not be actioned
4. Check current local changes before editing:

   ```bash
   git status --short
   ```

   Do not overwrite unrelated user changes. If touched files already contain edits, inspect them before modifying.

## Environment Setup

Use the Ubuntu environment.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm install
```

The local application is a Python web app with FastAPI, Jinja2, HTMX, handwritten CSS, and Playwright browser tests.

## Running The App

Start the single local Python process:

```bash
.venv/bin/python -m danish_rag.local_app
```

The default bind address is `127.0.0.1:8000`, controlled by `config/runtime-policy.json`.

The application must preserve the local-only answer path. Questions, retrieved evidence, generation, answers, indexes, and conversation history stay on the user's computer. Do not add answer-time web browsing or cloud calls unless an issue explicitly changes the approved product boundary.

## Daily Workflow

1. Sync your understanding from the issue, `CONTEXT.md`, and architecture docs.
2. Identify the smallest vertical change that satisfies the issue.
3. Add or update tests before or alongside behavior changes.
4. Keep implementation scoped to the relevant module:
   - `danish_rag/local_app.py` for local service and page routes
   - `danish_rag/provider_setup.py` and `danish_rag/runtime_*` for provider/runtime behavior
   - `danish_rag/retrieval.py` and `danish_rag/retrieval_benchmark.py` for retrieval
   - `danish_rag/answer_pipeline.py` for answer construction and evidence boundaries
   - `danish_rag/conversation_store.py` for local conversation persistence
   - `danish_rag/knowledge_release.py` for source/corpus release behavior
5. Update docs in the same change when behavior, commands, architecture, or evidence changes.
6. Run the relevant verification commands.
7. Record durable evidence in `docs/progress/` for issue-level benchmark, runtime, retrieval, or approval work.

## Testing

Run the smallest meaningful test set while developing, then broaden before completion.

Python unit and integration tests:

```bash
.venv/bin/python -m unittest
```

Targeted Python tests:

```bash
.venv/bin/python -m unittest tests.test_runtime_policy_contract -v
.venv/bin/python -m unittest tests.test_retrieval_benchmark -v
```

Browser tests:

```bash
npm run test:browser
```

Runtime provider gate:

```bash
.venv/bin/python -m danish_rag.runtime_probe --policy config/runtime-policy.json --evidence docs/progress/issue-26-runtime-probe.json
```

Retrieval benchmarks:

```bash
.venv/bin/python -m danish_rag.retrieval_benchmark --corpus data/retrieval_benchmark/corpus-fixtures.json --queries data/retrieval_benchmark/evaluation-queries.json --output docs/progress/issue-27-retrieval-benchmark.json
.venv/bin/python -m danish_rag.retrieval_benchmark --mode dense
.venv/bin/python -m danish_rag.retrieval_benchmark --mode compare
```

Live dense benchmark gate is opt-in because it depends on the local embedding provider:

```bash
DI_RAG_RUN_LIVE_DENSE_BENCHMARK=1 .venv/bin/python -m unittest tests.test_dense_retrieval_benchmark_live -v
```

If a verification command cannot be run, say exactly why and name the residual risk.

## Evidence And Documentation

Use project vocabulary from `CONTEXT.md`. Prefer:

- **Danish Immigration RAG**, not "Danish Immigration Assistant"
- **approved official source**, not "web source"
- **corpus**, not "knowledge base"
- **knowledge release**, not "live crawl" or "git pull"
- **Evidence Confidence** for evidence support, not model confidence
- **Fresh Tomato Score** for source freshness, not evidence confidence

Update these files when relevant:

- `README.md` for user-facing setup and command changes
- `docs/architecture.md` for approved architecture or decision summaries
- `docs/runtime-baseline.md` for runtime baseline changes
- `docs/source-governance.md` for approved source governance changes
- `docs/evaluation-quality-bar.md` and `config/evaluation-quality-bar.json` for quality-bar changes
- `docs/progress/issue-<number>-*.md` or `.json` for issue evidence
- `.agent/System/*.md` only when agent-facing system notes need to match current project reality

Do not present candidate evaluation packages, provisional benchmarks, or unapproved source-governance changes as final approval.

## Git Conventions

Branch names:

- `feat/<short-topic>`
- `fix/<short-topic>`
- `docs/<short-topic>`
- `test/<short-topic>`
- `refactor/<short-topic>`

Commit messages:

- Use concise, present-tense summaries.
- Mention the issue number when helpful.
- Do not add auto-generated signatures or unrelated formatting churn.

Examples:

```text
feat/runtime-provider-setup
fix/retrieval-eligibility-filter
docs/issue-29-hybrid-comparison
test/conversation-record-provenance
```

## Completion Checklist

Before claiming work is complete:

1. `git status --short` has been reviewed.
2. Relevant tests or benchmarks pass, or skipped checks are explicitly justified.
3. User-facing or agent-facing docs are updated when behavior changed.
4. Evidence files are updated for issue-level runtime, retrieval, evaluation, or approval work.
5. No unrelated user changes were reverted.
6. The final note names what changed, what was verified, and any remaining risk.
