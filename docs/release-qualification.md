# MVP Release Qualification

This document records the issue #25 release qualification for Danish Immigration RAG. The machine-readable source of truth is [config/release-qualification.json](../config/release-qualification.json). The embedded contract below is checked by tests so the release decision, distribution facts, model/runtime choices, active corpus, and privacy boundary cannot drift silently.

## Release Decision

Status: `blocked`.

Release decision: `do-not-release`.

The owner approved the production/fixture benchmark separation on 2026-09-08. Fresh production retrieval passes 15/15 required-evidence and critical cases; the unchanged fixture benchmark passes 7/7 eligible cases across all nine queries. The historical 2/7 candidate/fixture mismatch remains recorded as a diagnostic. Exact-packet independent human semantic review, privacy, rollback, supported-environment journeys, and manual accessibility evidence pass. Production sources are qualified and the owner confirmed recovery of the encrypted off-device signing-key backup. Final production release-owner approval remains pending; release is blocked.

<!-- release-qualification-contract:start -->
```json
{
  "qualification_id": "mvp-release-qualification-issue-25",
  "version": "0.5.0-blocked",
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
  "active_knowledge_release_id": "kr-2026-09-05.1",
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
- Included verification material: `config/trust_roots/project-release-key-v2.json` (public key only) and `data/source_registry/sr-2026-09-05.1.json`
- Included corpus path: `data/knowledge_releases/kr-2026-09-05.1/`
- Included operating documents: `docs/runtime-baseline.md`, `docs/source-governance.md`, `docs/evaluation-quality-bar.md`, and this document
- Excluded user data: `.venv/`, `__pycache__/`, local conversation stores, local provider configuration, derived local indexes, production-user questions, and production-user answers

Prerequisites are Python 3.11 or newer, Node.js with npm, OpenSSL with Ed25519 support, and Ollama 0.30.6 or newer for the approved local-provider baseline.

The launch command for this package is:

```bash
.venv/bin/python -c 'import uvicorn; from danish_rag.local_app import create_app; uvicorn.run(create_app(trust_root_path="config/trust_roots/project-release-key-v2.json"), host="127.0.0.1", port=8000)'
```

## Operating Instructions

Use Python 3.11 or newer and Node.js with npm. Create a virtual environment, install the Python and npm dependencies, then start one local process bound to `127.0.0.1`. Do not expose the application on a non-loopback host for the MVP candidate.

The documented setup path is:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm install
.venv/bin/python -c 'import uvicorn; from danish_rag.local_app import create_app; uvicorn.run(create_app(trust_root_path="config/trust_roots/project-release-key-v2.json"), host="127.0.0.1", port=8000)'
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

The active corpus requirement for this release candidate is `kr-2026-09-05.1`, with manifest `data/knowledge_releases/kr-2026-09-05.1/manifest.json` and source registry `sr-2026-09-05.1`.

The source registry assessment is production-qualified: all five official sources have recorded human production review, with no qualification reason codes. The corpus schema is `2.0`. The signing-key custody record includes owner-confirmed Google Drive backup and successful local recovery verification; the cloud account itself was not inspected. Source qualification and backup verification do not substitute for final release-owner approval.

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

Windows 11 with WSL2 Ubuntu on x86-64 is the only published MVP supported-environment target. The September live monitor passes all eight critical journeys using real loopback-bound processes and Playwright, including a verified restart and independently observed environment identity. Signed synthetic releases exercise update and rollback mechanics; they are not production publication evidence.

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

- Candidate retrieval evidence in `docs/progress/issue-51-retrieval-remediation.json` covers 6/6 residence and 5/5 registration requirements at limit 3 with no blocked sources. This intent-level coverage does not establish all-material Recall@3. The fresh candidate benchmark in `docs/progress/issue-51-candidate-retrieval-baseline.json` records 2/7 hits, five misses, zero blocked/forbidden results, and zero execution errors. Several frozen expectations reference fixture-only documents; they have not been excluded or remapped. This historical benchmark has no critical-case mapping; the newly approved production benchmark marks all fifteen cases critical. This historical diagnostic is preserved. It is superseded for qualification by the explicitly approved production/fixture contract below.
- `docs/progress/issue-51-reviewed-candidate-replay.json` records the exact reviewed candidate: 20/20 completed surfaces, 10 independent human reviews, six automated workflows, and no unevaluated gates. Required facts are 59/60 (98.3% against 95%); citations are 47/47 supported and unsupported-claim rate is zero. The failed fact judgment is preserved. No answers were regenerated for review replay.
- `docs/progress/issue-51-remediation-monitors.json` passes the strict privacy, six-phase rollback, and real-process supported-environment checks. Instrumented release transport and synthetic update fixtures do not imply a published production release.
- The September source registry assessment reports `production_release_eligible: true`. `docs/progress/issue-50-signing-key-reset.json` records signing-key custody and the owner-confirmed encrypted off-device backup recovery.
- The accessibility gate contains a hash-bound manual record for eight Narrator journeys on Chrome 152.0.7977.77 and Windows 11 25H2 build 26200.9278, alongside current automated browser evidence. Available-update review used an isolated synthetic release.
- Issue #7 and issue #24 decision/sign-off records remain approved. The owner explicitly approved a separate production retrieval dataset and all-critical mapping. Numerical thresholds, generation model, privacy contract, original answer dataset and all nine frozen fixture cases remain unchanged. Retrieval source coverage does not replace the independently reviewed answer-level fact and citation gates.
- Performance figures above are historical measurement baselines; they are not a new speed guarantee. No numeric latency SLA is configured.
- The owner-approved test updates are applied. The full Python suite passed on 2026-09-08: 502 tests, zero failures/errors, five skips. All 42 affected contract/evaluator tests passed. The release report now evaluates the approved production/fixture retrieval contract.
- The owner-approved 15-case production benchmark is `data/evaluation/production-retrieval-v1.json`; exact approval is recorded in `docs/progress/issue-51-production-retrieval-approval.json`. The proposal’s original `proposed-not-approved` field is retained verbatim for hash binding; approval comes from the separate decision record. All 15 cases are critical. Fresh `docs/progress/issue-51-approved-retrieval.json` records production Recall@3 15/15, critical Recall@3 15/15, and separate fixture Recall@3 7/7 across nine queries. Both scopes record zero blocked/forbidden violations. Results bind signed chunk IDs and the same inspected embedding identity. Safety checks cover the full fixture result list; only recall is limited to three. The historical 2/7 candidate/fixture diagnostic is unchanged.
- Final production release-owner approval remains pending. The release decision remains `do-not-release` until that explicit decision is recorded and the evaluator confirms all gates.

Any uncited official fact, personal eligibility conclusion, answer-path personal-data egress, failed atomic rollback, or mismatched active corpus/index pair blocks release.
