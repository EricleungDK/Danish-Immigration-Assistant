# Danish Immigration RAG Project Architecture

## Overview

Danish Immigration RAG is a local FastAPI web application for evidence-bounded
answers about Danish permanent-residence language requirements and Danish
examinations. Questions, retrieval, local model inference, answers, derived
indexes, and conversation records stay on the user's computer. Release discovery
and an explicitly approved knowledge-release download are separate, bounded
network operations.

The production baseline uses Ollama `gemma4:12b` for generation,
`embeddinggemma` for local embeddings, SQLite FTS5 plus local dense vectors, and
reciprocal-rank fusion with `k=60`. Retrieved metadata eligibility is applied
before evidence can receive credit. Structured answers are validated so every
official fact has eligible supporting citations; the model is never treated as
an official source.

Authoritative details: [`docs/architecture.md`](../../architecture.md),
[`docs/runtime-baseline.md`](../../runtime-baseline.md), and
[`docs/source-governance.md`](../../source-governance.md).

## Tech Stack

- Python 3.11+; FastAPI, Starlette, Uvicorn, Jinja2, and SQLite.
- Ollama 0.30.6+ on loopback, with separate generation and embedding adapters.
- Browser UI rendered server-side with HTMX, handwritten CSS/JavaScript, and
  Playwright plus axe-core verification.
- OpenSSL with Ed25519 support for exact-byte detached manifest signing and
  verification.
- GitHub Releases as the metadata/artifact authority for knowledge releases;
  application-code updates remain manual.

## Directory Structure

```text
danish_rag/                    production application and evaluation modules
danish_rag/web/                Jinja templates and local static assets
config/                        runtime, quality-bar, qualification, trust roots
data/knowledge_releases/       signed bundled knowledge releases
data/source_registry/          source-governance qualification evidence
data/evaluation/               approved synthetic evaluation cases
docs/                          authoritative architecture and progress evidence
tests/                         unit, integration, live opt-in, and browser gates
```

## Key Patterns

- `provider_setup.py`, `runtime_probe.py`, and `embedding_provider.py` keep
  provider capability and model identity contracts explicit.
- `retrieval.py` verifies corpus/index compatibility, filters ineligible sources,
  fuses FTS5/dense rankings, searches the reviewed Danish and English source
  languages, and prevents one chunk-heavy source from crowding out all others.
- `answer_pipeline.py` separates ambiguity, safety, generation, claim support,
  citations, Evidence Confidence, and Fresh Tomato Score.
- `conversation_store.py` persists immutable turn provenance locally.
- `release_trust.py`, `github_release_client.py`, and `knowledge_release.py`
  separate discovery, explicit download approval, signed review, install, and
  atomic rollback.
- `production_knowledge_release.py` binds completed source-review evidence into
  a production-qualified registry and caller-signed schema-2 release, then
  verifies installation, retrieval safety, and the five-stage rollback matrix.
- `final_answer_evaluation.py`, `release_monitors.py`, and
  `release_evaluation.py` fail closed when required live, workflow, or human
  evidence is absent or stale.

## Integration Points

- Ollama: loopback generation and embedding only; no remote fallback.
- Browser: loopback application origin with Host/Origin checks on mutations.
- GitHub Releases: content-free bounded metadata discovery and an asset download
  bound to explicit release/tag/asset approval.
- Official sources: fetched only by source-maintenance workflows; changed content
  cannot support answers before required human review and publication.

## Current Release Boundary

The reviewed-source build path is implemented and machine-tested from the five
completed issue-46 reviews. The bundled active corpus remains the prior
project-authored fixture because the matching production private signing key is
not present in the repository and no production candidate has been signed or
activated. Broader release publication also remains blocked by independent
final-answer adjudication, replacement real-process/browser environment
evidence, a manual assistive-technology check, and release-owner approval. See
[`docs/progress/issue-50-production-knowledge-release.md`](../../progress/issue-50-production-knowledge-release.md) and
[`docs/progress/source-registry-sr-2026-07-06.1.md`](../../progress/source-registry-sr-2026-07-06.1.md).
Completion evidence: [`../Reports/2026-07-14-mvp-completion-candidate.md`](../Reports/2026-07-14-mvp-completion-candidate.md).
