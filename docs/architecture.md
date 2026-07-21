# Danish Immigration RAG Architecture

This document records settled architecture plus project-level direction for Danish Immigration RAG. It is a decision summary, not an implementation plan; unresolved choices remain explicitly open.

## Scope And Traceability

- Runtime-baseline decisions proven by issue #26 are limited to the local provider baseline, generation/embedding capability separation, loopback defaults, release-network boundary, process/distribution baseline, first verified environment, and live structured-output probe. The traceable sources are [GitHub issue #26](https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/26), [docs/runtime-baseline.md](runtime-baseline.md), [docs/progress/issue-26-runtime-baseline.md](progress/issue-26-runtime-baseline.md), and the runtime sections of [.agent/issues/prd-runtime-and-retrieval-baseline.md](../.agent/issues/prd-runtime-and-retrieval-baseline.md).
- Retrieval architecture decisions approved by issue #4 are limited to the MVP hybrid retrieval baseline, metadata eligibility boundary, index compatibility requirements, and initial supported embedding model. The traceable sources are [GitHub issue #4](https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/4), [docs/progress/issue-29-hybrid-retrieval-comparison.md](progress/issue-29-hybrid-retrieval-comparison.md), [docs/progress/issue-29-hybrid-retrieval-recommendation.md](progress/issue-29-hybrid-retrieval-recommendation.md), and [docs/progress/issue-29-hybrid-retrieval-comparison.json](progress/issue-29-hybrid-retrieval-comparison.json).
- Source-governance decisions approved by issue #6 are limited to the human-reviewed source registry lifecycle, release manifest contents, source-state eligibility rules, signed-manifest integrity baseline, project trust-root requirement, maintainer roles, separation of duties, and recovery procedures. The traceable sources are [GitHub issue #6](https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/6), [GitHub issue #5](https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/5), [docs/source-governance.md](source-governance.md), [docs/progress/issue-5-source-governance.md](progress/issue-5-source-governance.md), and [docs/progress/issue-6-source-governance-approval.md](progress/issue-6-source-governance-approval.md).
- The issue #7 versioned evaluation package, existing metric definitions, configured release thresholds, hardware target, and supported-environment baseline were approved through the initiating product-owner instruction on 2026-07-13. The traceable sources are [GitHub issue #7](https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/7), [docs/evaluation-quality-bar.md](evaluation-quality-bar.md), [config/evaluation-quality-bar.json](../config/evaluation-quality-bar.json), [data/evaluation/evaluation-set-v0.1-candidate.json](../data/evaluation/evaluation-set-v0.1-candidate.json), and [docs/progress/issue-7-evaluation-quality-bar.md](progress/issue-7-evaluation-quality-bar.md).
- The interaction model, answer pipeline, and trust-indicator sections below preserve project-level context and pre-existing direction. They are not issue #26, issue #4, or issue #6 completion claims unless an item explicitly cites the approved runtime, retrieval, or source-governance baseline.
- Approval does not implement citation validation, answer evaluation, trust-scoring improvements, release tooling, missing numeric performance thresholds, or the critical-journey matrix; those require executable evidence.

## Product And Privacy Boundary

- The product is named **Danish Immigration RAG**. Renaming the GitHub repository to `danish-immigration-rag` remains a separate administrative task.
- The answer path is local-only: questions, retrieved evidence, model inference, answers, indexes, and conversation history do not leave the user's computer.
- Network access is limited to obtaining approved external source updates and project releases. There is no answer-time browsing.
- The MVP explains official requirements but does not calculate personal eligibility, maintain personal profiles, or act as a legal authority.
- Source documents remain in their original language. The MVP answers in English while preserving important Danish terms.

## Application Shape

- The MVP is a local web application rather than a packaged desktop application.
- Python is the working language for the application.
- FastAPI provides the local service, Jinja2 renders pages, HTMX handles targeted interactions, and handwritten CSS provides the visual layer.
- The production application should run as one local process serving both the web interface and application endpoints.
- A future desktop shell may wrap the local application, but desktop packaging is not part of the MVP.

## Interaction Model

These statements are project-level product direction. Issue #26 did not implement or approve production UI behavior.

- The primary interface is expected to be a calm, conversation-first experience rather than a source browser or research workbench.
- The desktop layout direction uses a narrow local-conversation sidebar and a flexible chat canvas. Official evidence is expected to open in a slide-over drawer instead of permanently competing with the answer for width.
- The large product prompt is an empty-state direction only. Active conversations are expected to use a compact title and status line plus a persistent multiline composer.
- The intended exchange rhythm is: user message, assistant identity and source status, natural-language answer, compact inline citations, optional support details, and suggested follow-up questions.
- The intended answer surface keeps only essential provenance visible: material-source count, Evidence Confidence, and source check date. Fresh Tomato Score explanations, corpus identity, freshness methodology, model identity, and update controls are expected to live in the evidence drawer.
- Official facts and interpretation should remain distinguishable through restrained margin labels. Colored callouts are reserved for warnings and explicit evidence-bounded refusals.
- The project direction was explored in the throwaway prototype at [`visualization/danish-rag-ui-prototype.html`](../visualization/danish-rag-ui-prototype.html). Prototype mechanics and styling are not production implementation requirements.

## Local Model Integration

- Users choose and run their own local model provider; Ollama is not mandatory.
- Issue #26 records Ollama 0.30.6+ as the first MVP provider baseline and `gemma4:12b` as the approved initial generation model. This does not make Ollama mandatory for future providers. See [docs/runtime-baseline.md](runtime-baseline.md) for the checked runtime contract.
- Provider-specific differences are isolated behind independent adapters rather than treated as perfectly interchangeable.
- Generation and embedding are separate capabilities and may use different providers or models.
- Provider selection is manual in the MVP and includes a connection test; automatic provider discovery is not required.
- Compatible local generation models remain configurable.
- Issue #4 approves `embeddinggemma` as the initial supported embedding model for the MVP retrieval baseline. It remains tied to the issue #29 benchmark evidence and may be replaced only through a later evaluated re-indexing decision.
- Each dense index records its embedding model, model identity, vector dimensions, corpus fixture identity, and schema version. Changing the embedding model, dimensions, corpus identity, or schema version requires re-indexing instead of mixing incompatible vectors.

## Local Data And Retrieval

Issue #4 approves the MVP retrieval baseline. Production release thresholds and broader evaluation targets remain deferred to the later evaluation decision ticket.

- Conversation history persists on the user's local disk.
- SQLite is the working store for conversations, messages, citations, model identity, corpus version, Evidence Confidence, and Fresh Tomato Score.
- MVP storage relies on per-user operating-system file permissions rather than application-level encryption.
- The approved MVP retrieval baseline is hybrid retrieval: SQLite FTS5 lexical retrieval, local dense retrieval using `embeddinggemma`, metadata eligibility filtering, and reciprocal-rank fusion with `k=60`.
- Metadata eligibility is applied before retrieval credit. Changed-unreviewed, broken, extraction-failed, and unapproved sources cannot support an answer; overdue but policy-usable sources remain distinguishable when allowed by policy.
- Corpus installations contain normalized documents and metadata, not a provider-specific prebuilt vector index.
- New and changed chunks are embedded locally into a compatibility-checked dense index. Corpus installation should show progress and preserve the previous usable corpus and index if re-indexing fails; detailed rollback mechanics remain deferred to implementation tickets.

## Source Governance And Updates

Issue #6 approves the source-governance operating model recommended by issue #5. Implementation tooling remains deferred, but the lifecycle, eligibility rules, manifest contents, signing baseline, maintainer roles, and recovery procedures are documented in [docs/source-governance.md](source-governance.md).

- Project maintainers own a human-reviewed source registry rather than allowing each installation to define trust independently.
- Maintainer automation may fetch approved URLs and detect changes, but changed, fetch-failed, broken, redirected, extraction-failed, overdue-blocked, withdrawn, superseded, and unapproved sources cannot support answers until an allowed human-review transition restores eligibility.
- GitHub Releases is the initial authority for versioned knowledge releases.
- After the page loads, the application uses a same-origin, loopback-validated POST to start a throttled GitHub Release metadata check. The ordinary page GET remains network-free, failures do not block local use, and the manual check remains available. Automatic checks never download or install artifacts; download and installation require separate explicit actions.
- The GitHub release transport lists bounded, content-free metadata separately from artifact retrieval. Artifact retrieval requires approval bound to the exact release tag, GitHub asset ID, and filename; one total deadline covers connection, redirect, and response-read work, with each blocking operation receiving only the remaining time. GitHub-controlled HTTPS origins, bounded byte counts, digest checks, no-overwrite atomic local writes, and partial-file cleanup define this network/filesystem boundary. Downloaded bytes are not unpacked or activated by the transport.
- Knowledge releases and application-code releases are independent. The application must not run `git pull` as an update mechanism.
- A knowledge release includes normalized documents, source URLs, final URLs, check timestamps, content hashes, normalized-document hashes, review status, reviewers, source registry version, corpus schema version, manifest schema version, and minimum compatible application version.
- The preferred integrity baseline is a signed release manifest with SHA-256 artifact hashes and a documented project trust root. Hash-only manifests are insufficient except as a temporary pre-signing MVP step.
- Release integrity is verified before installation, and installation is atomic with rollback on failure. The accessible Corpus status region polls only local state and renders the installer's actual progress callback events; it declares completion only when the backend reports a terminal event and the approved release is active. A withdrawal notice must block or warn on installed releases whose material sources are no longer trusted.
- Source review, release approval, publication, and recovery are human responsibilities assigned to the maintainer roles in [docs/source-governance.md](source-governance.md). A production knowledge release must record the named human maintainer or maintainers acting in those roles; the MVP fallback allows one maintainer to hold multiple roles only with visible audit notes and post-release review.
- The trust root is a project-controlled release-signing root distributed with the application or a separately verified project configuration. Key rotation, revoked key IDs, and emergency withdrawal handling must be documented before the first production knowledge release.

## Evaluation Quality Bar

Issue #7 has an approved versioned evaluation package in [docs/evaluation-quality-bar.md](evaluation-quality-bar.md), [config/evaluation-quality-bar.json](../config/evaluation-quality-bar.json), and [data/evaluation/evaluation-set-v0.1-candidate.json](../data/evaluation/evaluation-set-v0.1-candidate.json). Product owner approval provided through the initiating GPT goal instruction on 2026-07-13.

- The candidate dataset is `di-rag-eval-set-v0.1-candidate`, version `0.1.0-candidate`, with 20 project-authored synthetic cases covering happy paths, edge cases, out-of-bounds requests, ambiguity, conflicts, stale sources, refusals, and robustness.
- Retrieval evaluation and final-answer evaluation remain separate. A plausible generated answer cannot hide a retrieval miss.
- Proposed release-blocking thresholds cover retrieval, citations, unsupported claims, clarify/answer/refuse behavior, Evidence Confidence, Fresh Tomato Score, local-only privacy, update rollback, accessibility, reliability, runtime identity, and supported-environment critical journeys.
- Threshold weakening requires a new quality-bar version and recorded human approval.
- Baseline hardware evidence comes from issue #26 and issue #29: Windows 11 with WSL2 Ubuntu on x86-64, Python 3.12.3, Ollama 0.30.6, `gemma4:12b`, `embeddinggemma`, 16 CPU threads, and 15908 MB RAM.
- The only verified supported-environment candidate is Windows 11 with WSL2 Ubuntu on x86-64. Native Linux and macOS remain candidates pending full matrix evidence; native Windows is not supported for the MVP candidate.

## Answer Pipeline

This section is project-level answer-pipeline direction. Issue #26 proved only local structured output through the runtime provider; it did not implement production retrieval, prompting, answer validation, citation validation, or storage behavior.

- The MVP is expected to use a constrained RAG pipeline, not an autonomous agent loop.
- The intended pipeline normalizes the question, identifies ambiguity, retrieves approved evidence, rejects unsupported claims, generates a structured answer, validates citations, and stores the answer with its provenance.
- The generation model must not browse, choose arbitrary tools, or supply unsupported facts from its pretrained knowledge.
- When only part of a question is supported, the application should answer that portion and explicitly decline the unsupported portion.
- Official facts and interpretation should remain visibly distinct.

## Trust Indicators

This section is project-level trust-indicator direction. Issue #26 did not define scoring algorithms or implement historical answer storage.

- **Evidence Confidence** measures how directly and consistently retrieved approved sources support the answer. It is computed from evidence and citation coverage, not model self-rating.
- **Fresh Tomato Score** measures source recency and health independently of Evidence Confidence.
- Each source retains its own Fresh Tomato Score. The answer-level score is expected to be the lowest score among material sources.
- Trust indicators, their reasons, citations, model identity, and corpus version are expected to be stored with the historical answer rather than recalculated silently later.

## Still Open

- Final provider adapter contracts beyond the issue #26 Ollama baseline
- Retrieval release thresholds beyond the issue #4 MVP baseline
- Detailed corpus chunking, reranking, and rollback mechanics beyond the issue #4 MVP baseline
- Source-governance implementation tooling and exact signing command workflow
- Detailed browser security and local process lifecycle
- Application-code installation and update mechanism
- Numeric performance thresholds and executable supported-environment critical-journey evidence
- Production evaluation runner, answer evaluator, accessibility harness, network-boundary monitor, rollback fault injection, and supported-environment CI matrix
