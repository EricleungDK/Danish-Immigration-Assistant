# MVP Release Qualification

This document records the issue #25 release qualification for Danish Immigration RAG. The machine-readable source of truth is [config/release-qualification.json](../config/release-qualification.json). The embedded contract below is checked by tests so the release decision, distribution facts, model/runtime choices, active corpus, and privacy boundary cannot drift silently.

## Release Decision

Status: `blocked`.

Release decision: `do-not-release`.

The local application has a documented release candidate package, passing slice-level evidence, and an offline release evaluation runner, but the MVP release is blocked. The blocking reasons are:

- Issue #7 remains `candidate-ready-for-human-approval`; a human has not approved the evaluation dataset, metrics, thresholds, hardware targets, or supported-environment matrix.
- The final-answer evaluator, full network-boundary monitor, rollback fault-injection matrix, and supported-environment CI matrix are not implemented.
- The supported-environment critical journey matrix has not passed setup, supported answer, refusal, evidence inspection, history persistence, deletion/export, update installation, and rollback for every published supported environment.
- Performance baselines are published, but human-approved performance thresholds and full supported-environment measurements are not complete.
- Issue #24 human comprehension validation is still pending.

Do not weaken thresholds, remove blocking gates, or use production-user questions to manufacture release success.

## Policy Contract

<!-- release-qualification-contract:start -->
```json
{
  "qualification_id": "mvp-release-qualification-issue-25",
  "version": "0.1.0-blocked",
  "qualification_status": "blocked",
  "release_decision": "do-not-release",
  "quality_bar_version": "0.1.0-candidate",
  "quality_bar_approval_status": "candidate-ready-for-human-approval",
  "evaluation_dataset_id": "di-rag-eval-set-v0.1-candidate",
  "evaluation_dataset_version": "0.1.0-candidate",
  "application_distribution": "local-python-web-application",
  "application_process_model": "single-local-python-process",
  "default_bind_host": "127.0.0.1",
  "generation_model": "gemma4:12b",
  "embedding_model": "embeddinggemma",
  "active_knowledge_release_id": "kr-2026-07-06.1",
  "answer_path_allows_outbound_requests": false,
  "production_user_question_analytics_allowed": false
}
```
<!-- release-qualification-contract:end -->

## Distribution Package

The release candidate distribution is a local Python web application. It is not a desktop shell, background service, cloud service, or automatic updater.

Package identity:

- Candidate version: `0.1.0-rc.1`
- Application shape: single local Python process serving FastAPI, Jinja2, HTMX, and handwritten CSS
- Included application paths: `danish_rag/`, `requirements.txt`, `package.json`, `package-lock.json`
- Included policy and release paths: `config/runtime-policy.json`, `config/evaluation-quality-bar.json`, `config/release-qualification.json`
- Included corpus path: `data/knowledge_releases/kr-2026-07-06.1/`
- Included operating documents: `docs/runtime-baseline.md`, `docs/source-governance.md`, `docs/evaluation-quality-bar.md`, and this document
- Excluded user data: `.venv/`, `__pycache__/`, local conversation stores, local provider configuration, derived local indexes, production-user questions, and production-user answers

The launch command for this package is:

```bash
.venv/bin/python -m uvicorn danish_rag.local_app:create_app --factory --host 127.0.0.1 --port 8000
```

## Operating Instructions

Use Python 3.11 or newer. Create a virtual environment, install `requirements.txt`, then start one local process bound to `127.0.0.1`. Do not expose the application on a non-loopback host for the MVP candidate.

The documented setup path is:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn danish_rag.local_app:create_app --factory --host 127.0.0.1 --port 8000
```

Open the local browser at `http://127.0.0.1:8000`. Configure a local generation provider manually, test the connection, and keep provider settings local.

## Privacy Boundary

The local-only answer path keeps questions, retrieved evidence, model inference, answers, indexes, and conversation records on the user's computer. Answer-time browsing is not allowed.

The MVP requires no account, no cloud history, no remote inference credential, and no provider credential. Production-user questions are not analytics input. Evaluation uses project-authored synthetic cases and deliberately contributed test prompts, not production-user conversations.

Permitted release-network activity is limited to release discovery and approved knowledge-release artifact retrieval. Permitted update request fields are release metadata only; they must not contain questions, normalized questions, answers, evidence, conversation records, citation ids, turn indexes, prompts, messages, or stable conversation-derived identifiers.

## Model And Runtime

The first MVP provider baseline is Ollama 0.30.6 or newer on loopback. The initial generation model is `gemma4:12b`. The supported embedding baseline for the current retrieval architecture is `embeddinggemma`.

Generation and embedding remain separate capabilities. The generation model composes evidence-bounded answers from retrieved approved official sources; it is not itself an official source. Changing the embedding model requires a compatible local re-index, and incompatible vectors must not be reused.

Minimum hardware candidate:

- Windows 11 with WSL2 Ubuntu
- x86-64
- 16 GB system RAM
- Ollama 0.30.6 or newer
- `gemma4:12b`
- `embeddinggemma`
- Evergreen local browser

24 GB RAM is recommended when generation and indexing overlap. CPU-only latency is measured, not guaranteed.

## Corpus And Knowledge Releases

The active corpus requirement for this release candidate is `kr-2026-07-06.1`, with manifest `data/knowledge_releases/kr-2026-07-06.1/manifest.json` and source registry `sr-2026-07-06.1`.

Every material answer source must be a reviewed official source eligible under the source registry. An answer-supporting source may be `approved-current` or explicitly `overdue-policy-usable`. Changed, broken, redirected-pending-review, extraction-failed, overdue-blocked, withdrawn, superseded, and unapproved sources cannot support official facts.

The installed local index must match the active corpus, corpus schema version, embedding model, vector dimensions, and index schema version.

## Updates And Rollback

Knowledge-release discovery may inform the user about a newer reviewed release, but installation requires explicit user approval. Application-code updates are manual and remain separate from knowledge-release installation.

Installation verifies release identity, artifact hashes, schema compatibility, minimum application version, source review state, and local index compatibility before activation. If verification, extraction, embedding, indexing, or activation fails, rollback must preserve the previous usable corpus and index. A failed installation must not claim success.

## Recovery

Provider failures must identify the affected local provider and preserve the user's question and prior conversation for retry. Retrieval failures must name the local index or corpus problem without fabricating an answer. Storage failures must not report save, delete, or export success. Corpus activation failures must not leave a mismatched active corpus/index pair.

Recovery guidance is published in [docs/runtime-baseline.md](runtime-baseline.md), [docs/source-governance.md](source-governance.md), and the issue #22 regression tests.

## Support Boundary

Windows 11 with WSL2 Ubuntu on x86-64 is the only MVP supported verified candidate environment. It is not release-qualified until the full critical journey matrix passes.

macOS and native Linux remain candidates. Native Windows is not supported for the MVP candidate. Desktop packaging, background services, non-loopback exposure, cloud inference, cloud history, user uploads, production-user analytics, and automatic application-code updates are outside the MVP support boundary.

## Evaluation Results And Limitations

Evaluation dataset: `di-rag-eval-set-v0.1-candidate`, version `0.1.0-candidate`, 20 project-authored synthetic cases. Retrieval evaluation and final-answer evaluation remain separate.

Published release-blocking metrics include required evidence Recall@3, critical retrieval Recall@3, blocked-source violations, forbidden-result violations, official-fact citation coverage, citation correctness, unsupported-claim rate, required-fact coverage, clarify/answer/refuse behavior, trust-indicator correctness, Fresh Tomato minimum material-source behavior, privacy-network boundary, update rollback success, accessibility conformance, reliability critical journeys, runtime identity, supported-environment critical journeys, and performance.

The offline release evaluation runner publishes `docs/progress/release-evaluation-current.json` and evaluates every published release gate without running live provider, browser, or environment-matrix commands by default.

Release-blocking thresholds include:

- Required evidence Recall@3: at least `0.95`
- Official-fact citation coverage: `1.0`
- Unsupported-claim rate: `0.0`
- Answer-time personal-data egress: `0`
- Atomic update rollback success: `1.0`
- Accessibility: WCAG 2.2 AA with no critical or serious automated violations
- Critical journey pass rate: `1.0` across every published supported environment
- Performance: baseline results are published, but final release thresholds are pending human approval

Current performance baselines:

- Structured completion: `25805.935` ms
- Dense mean query latency: `146.602` ms
- Dense mean warm retrieval latency: `64.659` ms
- Dense indexing wall time: `1408.696` ms
- Dense index size: `151360` bytes
- Process peak resident memory: `101.434` MB

Current measured limitations:

- Hybrid retrieval required-evidence Recall@3 is `1.0` across 7 evaluable required-evidence queries in the 9-query issue #29 fixture set, with blocked-source violations `0` and forbidden-result violations `0`; the offline release evaluation runner records this as a passed retrieval gate.
- Human approval of issue #7 is pending.
- Human confirmation for issue #24 usability validation is pending.
- Human-approved performance thresholds and full supported-environment measurements are pending.
- Release remains blocked by missing final-answer evaluation, full network-boundary monitoring, rollback fault-injection coverage, supported-environment matrix completion, human approval, and approved performance thresholds.

Any uncited official fact, personal eligibility conclusion, answer-path personal-data egress, failed atomic rollback, or mismatched active corpus/index pair blocks release.
