# MVP Release Qualification

This document records the issue #25 release qualification for Danish Immigration RAG. The machine-readable source of truth is [config/release-qualification.json](../config/release-qualification.json). The embedded contract below is checked by tests so the release decision, distribution facts, model/runtime choices, active corpus, and privacy boundary cannot drift silently.

## Release Decision

Status: `blocked`.

Release decision: `do-not-release`.

The local application has a documented release candidate package and live evidence for several release gates, but the MVP release is blocked. The blocking reasons are:

- Independent-human adjudications have not been supplied for required-fact coverage, forbidden claims, privacy prose, citation correctness, and unsupported-claim rate. The live final-answer report therefore remains non-strict even though every machine-evaluable gate passes.
- The source registry is not production-qualified. It records no curator admissions, monitoring records, archived official-source snapshots, named human production reviews, or durable production signing-key custody record for the current project-authored fixtures.
- The published supported-environment evidence used an in-process ASGI transport, did not restart the application for persistence, and copied rather than observed environment identity. It cannot qualify the required real-process/browser journey matrix and must be replaced.
- The earlier automated accessibility run passed, but UI code changed afterward and the current Playwright suite has not been rerun. The quality bar also requires an actual manual assistive-technology check; no such check has been performed or recorded.
- Final production release-owner approval is pending.

The 2026-07-14 live final-answer run completed all 20 cases with zero execution errors and passed every machine-evaluable structural, source, behavior, trust, freshness, personal-eligibility, and workflow gate. The live privacy boundary and all six rollback fault phases pass. The previous eight-journey supported-environment claim is withdrawn pending replacement real-process/browser evidence. Performance measurement completeness also passes; no numeric latency SLA is configured, so no numeric speed threshold is invented.

Product owner approval provided through the initiating GPT goal instruction on 2026-07-13 for the existing issue #7 dataset, metrics, configured thresholds, hardware target, supported-environment baseline, and issue #24 usability/comprehension sign-off. This removes those decision gates only and does not mark any implementation or test gate passed.

Do not weaken thresholds, remove blocking gates, or use production-user questions to manufacture release success.

## Policy Contract

<!-- release-qualification-contract:start -->
```json
{
  "qualification_id": "mvp-release-qualification-issue-25",
  "version": "0.4.0-blocked",
  "qualification_status": "blocked",
  "release_decision": "do-not-release",
  "quality_bar_version": "0.1.0-candidate",
  "quality_bar_approval_status": "approved",
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

Prerequisites are Python 3.11 or newer, Node.js with npm, OpenSSL with Ed25519 support, and Ollama 0.30.6 or newer for the approved local-provider baseline.

The launch command for this package is:

```bash
.venv/bin/python -m uvicorn danish_rag.local_app:create_app --factory --host 127.0.0.1 --port 8000
```

## Operating Instructions

Use Python 3.11 or newer and Node.js with npm. Create a virtual environment, install the Python and npm dependencies, then start one local process bound to `127.0.0.1`. Do not expose the application on a non-loopback host for the MVP candidate.

The documented setup path is:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm install
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

That source registry is intentionally fail-closed for production qualification. Its machine-readable assessment reports `production_release_eligible: false`: the current documents are project-authored fixtures and the required official snapshots, curator and monitoring records, and human production reviews have not been recorded. A signed manifest does not substitute for source review.

Every material answer source must be a reviewed official source eligible under the source registry. An answer-supporting source may be `approved-current` or explicitly `overdue-policy-usable`. Changed, broken, redirected-pending-review, extraction-failed, overdue-blocked, withdrawn, superseded, and unapproved sources cannot support official facts.

The installed local index must match the active corpus, corpus schema version, embedding model, vector dimensions, and index schema version.

## Updates And Rollback

Knowledge-release discovery may inform the user about a newer reviewed release, but installation requires explicit user approval. Application-code updates are manual and remain separate from knowledge-release installation.

Installation verifies release identity, artifact hashes, schema compatibility, minimum application version, source review state, and local index compatibility before activation. If verification, extraction, embedding, indexing, or activation fails, rollback must preserve the previous usable corpus and index. A failed installation must not claim success.

The GitHub Releases flow presents content-free metadata first, downloads only after exact explicit approval, safely stages and verifies the bounded archive and signed manifest/tag identity, presents the signed review summary, and requires a separate explicit install/activation action. The isolated transport applies network and filesystem limits. The strict monitor instrumented the default client's `OpenerDirector.open` boundary using in-memory responses: it observed metadata discovery and approved artifact retrieval, verified content-free fields, and proved that an unapproved artifact request is blocked before transport. This validates the transport path without claiming that a reviewed production release was contacted, created, or published.

## Recovery

Provider failures must identify the affected local provider and preserve the user's question and prior conversation for retry. Retrieval failures must name the local index or corpus problem without fabricating an answer. Storage failures must not report save, delete, or export success. Corpus activation failures must not leave a mismatched active corpus/index pair.

Recovery guidance is published in [docs/runtime-baseline.md](runtime-baseline.md), [docs/source-governance.md](source-governance.md), and the issue #22 regression tests.

## Support Boundary

Windows 11 with WSL2 Ubuntu on x86-64 is the only published MVP supported-environment target. It is not yet release-qualified. The earlier 2026-07-14 monitor used in-process transport, did not observe a real application restart, and did not independently observe the browser/environment identity. A replacement live run must exercise the hardened two-process Playwright journey and pass the independent evidence checks before this environment can qualify.

macOS and native Linux remain candidates. Native Windows is not supported for the MVP candidate. Desktop packaging, background services, non-loopback exposure, cloud inference, cloud history, user uploads, production-user analytics, and automatic application-code updates are outside the MVP support boundary.

## Evaluation Results And Limitations

Evaluation dataset: `di-rag-eval-set-v0.1-candidate`, version `0.1.0-candidate`, 20 project-authored synthetic cases. Retrieval evaluation and final-answer evaluation remain separate.

Published release-blocking metrics include required evidence Recall@3, critical retrieval Recall@3, blocked-source violations, forbidden-result violations, official-fact citation coverage, citation correctness, unsupported-claim rate, required-fact coverage, clarify/answer/refuse behavior, trust-indicator correctness, Fresh Tomato minimum material-source behavior, privacy-network boundary, update rollback success, accessibility conformance, reliability critical journeys, runtime identity, supported-environment critical journeys, and performance.

Human approval of the existing issue #7 and issue #24 decision records was provided through the initiating GPT goal instruction on 2026-07-13. It does not substitute for the results below.

The offline release evaluation runner publishes `docs/progress/release-evaluation-current.json` and evaluates every published release gate without running live provider, browser, or environment-matrix commands by default.

Release-blocking thresholds include:

- Required evidence Recall@3: at least `0.95`
- Official-fact citation coverage: `1.0`
- Unsupported-claim rate: `0.0`
- Answer-time personal-data egress: `0`
- Atomic update rollback success: `1.0`
- Accessibility: WCAG 2.2 AA with no critical or serious automated violations, full keyboard coverage, and the required manual assistive-technology check
- Critical journey pass rate: `1.0` across every published supported environment
- Performance: no numeric latency SLA is configured; current measurement completeness is required and passed

Current performance baselines:

- Structured completion: `23509.025` ms
- Dense mean query latency: `143.614` ms
- Dense mean warm retrieval latency: `63.642` ms
- Dense indexing wall time: `1371.92` ms
- Dense index size: `151360` bytes
- Process peak resident memory: `398.898` MB

Current evidence and limitations:

- Hybrid retrieval required-evidence Recall@3 is `1.0` across 7 evaluable required-evidence queries in the 9-query issue #29 fixture set, with blocked-source violations `0` and forbidden-result violations `0`; the offline release evaluation runner records this as a passed retrieval gate.
- The live strict network monitor observed all nine required workflows with zero forbidden requests. Through instrumented in-memory responses it observed default-client GitHub discovery and approved retrieval, confirmed content-free release fields, and blocked unapproved artifact transport before any response; no production release was contacted or published.
- The live strict rollback matrix passed verification, extraction, embedding, indexing, activation, and late-activation fault injection while retaining the prior queryable corpus/index pair.
- The previous supported-environment monitor exercised all eight journey checks, but it ran the application in process, did not restart it for persistence, and copied environment identity from policy. That report is retained as diagnostic evidence but does not qualify the real-process/browser environment gate.
- The live final-answer evaluator completed 20 of 20 cases with zero errors. Behavior passed on all 14 applicable cases; structural/source-domain, official-fact citation coverage, trust, freshness, personal-eligibility, and automated workflow checks also pass.
- The final-answer report has `strict_passed: false` only because required-fact coverage, forbidden claims, privacy prose, citation correctness, and unsupported-claim rate still require independent-human adjudication. No semantic pass was inferred.
- The source registry has `production_release_eligible: false` because required production source-governance evidence, human reviews, and durable off-repository production signing-key custody are absent.
- The prior automated axe, keyboard, reflow, reduced-motion, and non-color run passed before later UI changes. Current automation is `stale-reverification-required`, and the required actual assistive-technology check remains `not_verified`; both block release.
- Issue #7 and issue #24 decision/sign-off records were approved through the initiating product-owner instruction on 2026-07-13; no participant details or new test results are implied.
- Performance measurements are recorded. No numeric latency SLA is configured, so measurement completeness passes without asserting a speed guarantee or weakening another threshold.
- Release remains blocked by missing independent-human final-answer adjudication, the unqualified production source registry, replacement real-process/browser supported-environment evidence, the required manual assistive-technology check, and final production release-owner approval.

Any uncited official fact, personal eligibility conclusion, answer-path personal-data egress, failed atomic rollback, or mismatched active corpus/index pair blocks release.
