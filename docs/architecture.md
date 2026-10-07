# Danish Immigration RAG Architecture

This document records settled architecture and current implementation status for
Danish Immigration RAG. Historical direction is retained where useful, and
remaining choices are explicitly separated from the release-qualified MVP.

## Scope And Traceability

- Runtime-baseline decisions proven by issue #26 are limited to the local provider baseline, generation/embedding capability separation, loopback defaults, release-network boundary, process/distribution baseline, first verified environment, and live structured-output probe. The traceable sources are the [parent runtime decision issue #2](https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/2), [implementation issue #26](https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/26), [docs/runtime-baseline.md](runtime-baseline.md), and [docs/progress/issue-26-runtime-baseline.md](progress/issue-26-runtime-baseline.md).
- Retrieval architecture decisions approved by issue #4 are limited to the MVP hybrid retrieval baseline, metadata eligibility boundary, index compatibility requirements, and initial supported embedding model. The traceable sources are [GitHub issue #4](https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/4), [docs/progress/issue-29-hybrid-retrieval-comparison.md](progress/issue-29-hybrid-retrieval-comparison.md), [docs/progress/issue-29-hybrid-retrieval-recommendation.md](progress/issue-29-hybrid-retrieval-recommendation.md), and [docs/progress/issue-29-hybrid-retrieval-comparison.json](progress/issue-29-hybrid-retrieval-comparison.json).
- Source-governance decisions approved by issue #6 are limited to the human-reviewed source registry lifecycle, release manifest contents, source-state eligibility rules, signed-manifest integrity baseline, project trust-root requirement, maintainer roles, separation of duties, and recovery procedures. The traceable sources are [GitHub issue #6](https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/6), [GitHub issue #5](https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/5), [docs/source-governance.md](source-governance.md), [docs/progress/issue-5-source-governance.md](progress/issue-5-source-governance.md), and [docs/progress/issue-6-source-governance-approval.md](progress/issue-6-source-governance-approval.md).
- The issue #7 versioned evaluation package, existing metric definitions, configured release thresholds, hardware target, and supported-environment baseline were approved through the initiating product-owner instruction on 2026-07-13. The traceable sources are [GitHub issue #7](https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/7), [docs/evaluation-quality-bar.md](evaluation-quality-bar.md), [config/evaluation-quality-bar.json](../config/evaluation-quality-bar.json), [data/evaluation/evaluation-set-v0.1-candidate.json](../data/evaluation/evaluation-set-v0.1-candidate.json), and [docs/progress/issue-7-evaluation-quality-bar.md](progress/issue-7-evaluation-quality-bar.md).
- The interaction model, answer pipeline, and trust-indicator sections below preserve the original project direction while also describing the current implementation. Their acceptance evidence is maintained in [docs/release-qualification.md](release-qualification.md) and the linked issue-progress records.
- The issue approvals did not themselves implement citation validation, answer evaluation, trust scoring, release tooling, or critical-journey checks; those implementation and qualification steps are now recorded separately in the current evidence documents.

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

The following product direction is implemented in the current application. The
original prototype remains design history, not a production implementation
requirement.

- The primary interface is a calm, conversation-first experience rather than a source browser or research workbench.
- The desktop layout uses a narrow local-conversation sidebar and a flexible chat canvas. Official evidence opens in a slide-over drawer instead of permanently competing with the answer for width.
- The large product prompt is an empty-state surface. Active conversations use a compact title and status line plus a persistent multiline composer.
- The exchange rhythm is: user message, assistant identity and source status, natural-language answer, compact inline citations, optional support details, and suggested follow-up questions.
- Exact greetings are handled locally without retrieval or generation. Clearly non-domain conversation may use the configured local generation model in a separate fact-bounded social mode with no corpus evidence or trust indicators; factual answers remain limited to approved official evidence.
- The answer surface keeps only essential provenance visible: material-source count, Evidence Confidence, and source check date. Fresh Tomato Score explanations, corpus identity, freshness methodology, model identity, and update controls live in the evidence drawer.
- Official facts and interpretation remain distinguishable through restrained margin labels. Colored callouts are reserved for warnings and explicit evidence-bounded refusals.
- The project direction was explored in the throwaway prototype at [`visualization/danish-rag-ui-prototype.html`](../visualization/danish-rag-ui-prototype.html). Prototype mechanics and styling are not production implementation requirements.

## Local Model Integration

- Users choose and run their own local model provider; Ollama is not mandatory.
- Issue #26 records Ollama 0.30.6+ as the first MVP provider baseline and `gemma4:12b` as the approved initial generation model. This does not make Ollama mandatory for future providers. See [docs/runtime-baseline.md](runtime-baseline.md) for the checked runtime contract.
- Provider-specific differences are isolated behind independent adapters rather than treated as perfectly interchangeable.
- Generation and embedding are separate capabilities and may use different providers or models.
- Provider selection remains manual and includes a connection test. Following the
  2026-07-23 revised product requirement, setup can explicitly query the selected
  loopback provider for installed model choices; this is user-initiated provider
  inventory discovery, not automatic provider discovery.
- Compatible local generation models remain configurable. Ollama inventory
  discovery excludes cloud-tagged, embedding-only, and models that do not match
  the approved generation identity/capability contract; the selected model still
  must pass the full connection/capability test before it is saved.
- Issue #4 approves `embeddinggemma` as the initial supported embedding model for the MVP retrieval baseline. It remains tied to the issue #29 benchmark evidence and may be replaced only through a later evaluated re-indexing decision.
- Each dense index records its embedding model, model identity, vector dimensions, corpus identity, knowledge-release identity, and schema version. Chunked indexes additionally bind the corpus schema and semantic content-unit schema. Changing any compatibility identity requires re-indexing instead of mixing incompatible vectors.

## Local Data And Retrieval

Issue #4 approves the MVP retrieval baseline. The current implementation uses
that baseline, and the issue #7 evaluation package plus issue #51 evidence now
record the approved release thresholds and qualification results.

- Conversation history persists on the user's local disk.
- SQLite is the working store for conversations, messages, citations, model identity, corpus version, Evidence Confidence, and Fresh Tomato Score.
- MVP storage relies on per-user operating-system file permissions rather than application-level encryption.
- The approved MVP retrieval baseline is hybrid retrieval: SQLite FTS5 lexical retrieval, local dense retrieval using `embeddinggemma`, metadata eligibility filtering, and reciprocal-rank fusion with `k=60`.
- Metadata eligibility is applied before retrieval credit. Changed-unreviewed, broken, extraction-failed, and unapproved sources cannot support an answer; overdue but policy-usable sources remain distinguishable when allowed by policy.
- Topic metadata detected from a question is represented as alternative intent groups. Tags within one intent group remain conjunctive, while eligible evidence may match any group. Result selection reserves the highest-ranked eligible evidence for each detected intent before filling remaining capacity by fused rank, so one stronger intent cannot crowd another out. For schema-2 semantic chunks, remaining result slots prefer the highest-ranked chunks from distinct eligible sources before additional chunks from an already represented source. Selected results retain their fused rank order; this prevents repeated chunks from crowding out another relevant official source.
- Corpus installations contain normalized content and metadata, not a provider-specific prebuilt vector index. Corpus schema `1.0` remains the supported whole-document format. Corpus schema `2.0` declares `semantic-chunk-v1` content units and cannot be interpreted as schema `1.0`.
- Schema `2.0` release authoring first verifies the supplied normalized content against its reviewed hash, then divides it at deterministic semantic boundaries. Each chunk ID is derived from its approved source ID, source-document ID, chunk content hash, and duplicate occurrence. Signed-release verification reconstructs the ordered chunks and rechecks the reviewed normalized-content hash before activation.
- Whole documents use `hybrid-index-v1`; semantic chunks use `hybrid-chunk-index-v1` with the corpus and content-unit schema recorded in index compatibility metadata. Both use the approved hybrid retrieval baseline.
- Reviewed production-release authoring consumes the completed source-review bundle directly, semantically cross-checks its flattened completion record against the bound human decisions, binds its machine manifest, official snapshots, normalized extractions, approved URLs, and named source/release governance evidence, then signs and cross-checks the resulting source registry and schema-2 release. Candidate output paths are exclusively claimed and partial builds are removed on failure. The production private key remains an explicit off-repository input.
- Chunk retrieval searches both reviewed Danish and English source documents and prefers distinct approved sources before filling remaining result positions with additional chunks from a source. This prevents a long page from crowding smaller reviewed sources out of a bounded result set.
- Semantic-chunk retrieval also fuses source-title relevance as a third ranking channel with the same RRF `k=60`. It scores actual-question/title token overlap with source-level inverse document frequency, excludes common function words, and gives one title vote to each source's strongest already-retrieved chunk. This preserves source-title relevance without multiplying it by chunk count or changing signed corpus/index bytes. Residence and enrolment paraphrases retain separate intent reservations; title ranking cannot bypass source eligibility. Within-source passage preference and remaining-slot coverage preserve examinations explicitly named in the actual question; source metadata alone cannot satisfy passage-level subject coverage. Broad Danish-examination requests prefer passages covering the examination family. Lexical normalization retains exam-level digits and removes English function words before its bounded term limit. For an explicit certificate-equivalence intent, an earlier same-source list-introduction colon can nominate a separate exact context chunk within the existing top-three budget, after intent reservations and named-exam coverage. This bounded relevance heuristic can replace redundant source-diversity fill; it does not infer list membership from the flattened corpus. Qualification evidence binds retrieval and collector implementation hashes alongside corpus/index identity. See [issue #51 retrieval remediation](progress/issue-51-retrieval-remediation.md).
- Installation builds and validates the new corpus/index pair in private staging. Verification, extraction, embedding, indexing, compatibility, or activation failure removes that staging directory and leaves the prior usable pair active and queryable. Activation updates future answer provenance only; persisted historical citations remain unchanged.

## Source Governance And Updates

Issue #6 approves the source-governance operating model recommended by issue #5.
The implementation now applies the lifecycle, eligibility rules, manifest
contents, signing baseline, maintainer roles, and recovery procedures documented
in [docs/source-governance.md](source-governance.md).

- Project maintainers own a human-reviewed source registry rather than allowing each installation to define trust independently.
- Maintainer automation may fetch approved URLs and detect changes, but changed, fetch-failed, broken, redirected, extraction-failed, overdue-blocked, withdrawn, superseded, and unapproved sources cannot support answers until an allowed human-review transition restores eligibility.
- GitHub Releases is the initial authority for versioned knowledge releases.
- After the page loads, the application uses a same-origin, loopback-validated POST to start a throttled GitHub Release metadata check. The ordinary page GET remains network-free, failures do not block local use, and the manual check remains available. Automatic checks never download or install artifacts; download and installation require separate explicit actions.
- The GitHub release transport lists bounded, content-free metadata separately from artifact retrieval. Artifact retrieval requires approval bound to the exact release tag, GitHub asset ID, and filename; one total deadline covers connection, redirect, and response-read work, with each blocking operation receiving only the remaining time. GitHub-controlled HTTPS origins, bounded byte counts, digest checks, no-overwrite atomic local writes, and partial-file cleanup define this network/filesystem boundary. Downloaded bytes are not unpacked or activated by the transport.
- Knowledge releases and application-code releases are independent. The application must not run `git pull` as an update mechanism.
- A knowledge release includes normalized documents, source URLs, final URLs, check timestamps, content hashes, normalized-document hashes, review status, reviewers, source registry version, corpus schema version, manifest schema version, and minimum compatible application version.
- A production semantic-chunk release additionally carries the exact reviewed normalized-extraction hash through its signed manifest, retrieval results, citations, and registry cross-check. This distinguishes the archived reviewed extraction from the deterministic whitespace-normalized document identity used to reconstruct chunk sequences.
- The preferred integrity baseline is a signed release manifest with SHA-256 artifact hashes and a documented project trust root. Hash-only manifests are insufficient except as a temporary pre-signing MVP step.
- Release integrity is verified before installation, and installation is atomic with rollback on failure. The accessible Corpus status region polls only local state and renders the installer's actual progress callback events; it declares completion only when the backend reports a terminal event and the approved release is active. A withdrawal notice must block or warn on installed releases whose material sources are no longer trusted.
- Production-candidate retrieval qualification pins one copy of the release, installs and indexes it into an isolated local directory, and runs a fixed five-case suite. Required-source misses, blocked sources, forbidden source-document results, or a substituted qualification suite prevent activation in the target data directory. The final atomic install uses that same pinned copy and still protects the prior active release.
- Source review, release approval, publication, and recovery are human responsibilities assigned to the maintainer roles in [docs/source-governance.md](source-governance.md). A production knowledge release must record the named human maintainer or maintainers acting in those roles; the MVP fallback allows one maintainer to hold multiple roles only with visible audit notes and post-release review.
- The active trust root is the project-controlled `project-release-key-v2` root distributed with the application. Key rotation, revoked key IDs, and emergency withdrawal handling are documented in [docs/source-governance.md](source-governance.md) and [the signing-key evidence](progress/issue-50-signing-key-reset.json).

## Evaluation Quality Bar

Issue #7 has an approved versioned evaluation package in [docs/evaluation-quality-bar.md](evaluation-quality-bar.md), [config/evaluation-quality-bar.json](../config/evaluation-quality-bar.json), and [data/evaluation/evaluation-set-v0.1-candidate.json](../data/evaluation/evaluation-set-v0.1-candidate.json). Product owner approval provided through the initiating GPT goal instruction on 2026-07-13.

- The candidate dataset is `di-rag-eval-set-v0.1-candidate`, version `0.1.0-candidate`, with 20 project-authored synthetic cases covering happy paths, edge cases, out-of-bounds requests, ambiguity, conflicts, stale sources, refusals, and robustness.
- Retrieval evaluation and final-answer evaluation remain separate. A plausible generated answer cannot hide a retrieval miss.
- Approved release-blocking thresholds cover retrieval, citations, unsupported claims, clarify/answer/refuse behavior, Evidence Confidence, Fresh Tomato Score, local-only privacy, update rollback, accessibility, reliability, runtime identity, and supported-environment critical journeys.
- Threshold weakening requires a new quality-bar version and recorded human approval.
- Baseline hardware evidence comes from issue #26 and issue #29: Windows 11 with WSL2 Ubuntu on x86-64, Python 3.12.3, Ollama 0.30.6, `gemma4:12b`, `embeddinggemma`, 16 CPU threads, and 15908 MB RAM.
- The documented and qualified MVP supported target is Windows 11 with WSL2 Ubuntu on x86-64. Native Linux and macOS remain candidates pending full matrix evidence; native Windows is not supported for the MVP candidate.

## Answer Pipeline

The current application implements this constrained answer pipeline. Issue #26
proved the local structured-output runtime baseline; later implementation and
qualification evidence covers retrieval, prompting, answer validation, citation
validation, storage, and release behavior.

- The MVP uses a constrained RAG pipeline, not an autonomous agent loop.
- The intended pipeline normalizes the question, identifies ambiguity, retrieves approved evidence, rejects unsupported claims, generates a structured answer, validates citations, and stores the answer with its provenance.
- General examination-term questions can include documented relevance to
  permanent-residence language requirements as an explicitly displayed context.
  The same application-derived interpretation reaches both retrieval and
  generation, while the original question remains in the conversation record.
  An explicit different purpose or logistics task does not gain that context;
  consequential or unspecified application ambiguity still requires clarification.
- A pre-retrieval conversation router keeps greetings and clearly non-domain turns out of hybrid retrieval. The separate social-generation schema prohibits external factual, immigration, legal, eligibility, and examination claims; it does not turn the generation model into a factual source.
- The generation model must not browse, choose arbitrary tools, or supply unsupported facts from its pretrained knowledge.
- When only part of a question is supported, the application answers that portion and explicitly declines the unsupported portion.
- Official facts and interpretation remain visibly distinct.
- Production local-provider answers use a separate local verification request to
  check the summary and every model-authored section against exact spans of the
  retrieved evidence. The displayed summary repeats the first official-fact section, or
  the refusal when no official fact exists, before verification; it cannot add
  a separate generated claim. This supports English translations of Danish sources without
  treating English/Danish word overlap as entailment. The application checks
  complete coverage, citation and source-byte bindings, examination identities,
  numeric fidelity using recorded source language, and polarity. A bounded
  protocol correction can repair invalid witness selection; a negative semantic
  verdict is not retried as a verifier protocol error. One bounded answer repair
  may regenerate a rejected answer, and exhausted or invalid checks fail closed.
  Verification uses the same configured local model in a separate request, with
  an explicit 8192-token Ollama context for verification and answer repair.
  Repair retains the original evidence and rejected answer. It is model-based runtime evidence, not
  independent human adjudication or a guarantee of semantic correctness. Direct
  validation and controlled fixtures retain the deterministic lexical guard;
  untrusted generated JSON cannot supply the internal verification bindings.
- Explicitly cited chunks may jointly support a claim only when their complete
  source-document provenance matches, including source and document identities,
  content and extraction hashes, URLs, language, corpus, and release. Missing or
  invalid provenance keeps chunks separate. Every cited chunk must contribute an
  exact witness; uncited context cannot supply a missing qualification. Shared
  provenance alone does not establish a list relationship across missing text.
  Generation receives these verified groups and the application safety boundary
  so it can explain supported general rules without deciding personal eligibility.
  Generator requests use short, unique citation labels to distinguish similar
  chunk identifiers. Initial and repair outputs map through the same exact
  bijection back to canonical IDs before verification; unknown and duplicate
  labels fail closed. Saved provenance retains the canonical signed-corpus IDs.
  A declared package label can explicitly select multiple retrieved chunks from
  one verified document, when every member is allowed by the caller. Selecting
  that label expands only its listed members; selecting an individual label
  never adds context. Duplicate or overlapping selections fail closed. Repair
  represents earlier citations individually, without inferring a package.
  Expanded citations undergo the same per-chunk material-witness checks.
  Repair can receive bounded, item-specific missing exam/number-anchor details
  under the existing guard rules. Suggested packages must already be declared,
  caller-allowed, from the currently cited document, and cover the deficit.
  Anchor coverage does not establish entailment or add a citation; the writer
  must explicitly revise, and ordinary verification still applies.
  The human review page displays the same explicitly cited context and binds each
  judgment to the complete cited group; earlier isolated-citation judgments are
  not silently reused.
- After the complete model answer passes verification, the application appends
  its own safety refusals and source-status warnings from the safety decision
  and validated evidence metadata. These program-derived statements receive
  local bindings, rather than asking a source-text verifier to prove application
  policy or review metadata. No model-supplied section kind or origin flag grants
  this treatment. Final validation checks the exact complete binding set,
  including every appended section and the derived summary.
- After the single answer repair is exhausted, a complete typed verification
  response may support an explicitly partial answer. It retains only unchanged,
  self-contained official facts with positive semantic verdicts and valid exact
  witnesses under the ordinary guards. Text rejected elsewhere in the same
  response cannot be reintroduced through a duplicate or the summary. Partial
  success requires at least one qualifying fact; incomplete or malformed
  verification, transport failure, and resource failure remain errors.
  Safety and source warnings are reconstructed,
  the application discloses incomplete verification, and stored/captured
  metadata records retained and omitted counts. This does not establish
  required-fact coverage or replace independent human review.
- Witnesses use exact contiguous whole-sentence ranges, preserving chains of
  referential context. At most 16 adjacent sentence groups per retrieved chunk
  bound enumeration. Each item retains its eligible base groups, up to eight
  contextual ranges, and the full-chunk candidate. Number and examination-name
  candidate filters are rechecked at validation; they do not establish semantic
  entailment. Affirmative claims cannot select negative witness ranges that the
  unchanged final polarity guard necessarily rejects. Governing referential
  context remains intact, and negative claims keep their existing candidates.
  Every verifier request, including a protocol correction, rejects
  message content exceeding 24,000 UTF-8 bytes before provider execution. This
  resource bound is not exact model-token accounting or proof of context fit.
  Compact JSON and request-local source-group labels reduce repeated protocol
  overhead without removing evidence or changing the internal provenance keys.

## Trust Indicators

The current implementation computes and persists these trust indicators with
each historical answer. The issue #26 runtime decision did not define these
algorithms; the later answer-pipeline, persistence, and release-evaluation
evidence does.

- **Evidence Confidence** measures how directly and consistently retrieved approved sources support the answer. It is computed from evidence and citation coverage, not model self-rating.
- **Fresh Tomato Score** measures source recency and health independently of Evidence Confidence.
- Each source retains its own Fresh Tomato Score. The answer-level score is the lowest score among material sources.
- Trust indicators, their reasons, citations, model identity, and corpus version are stored with the historical answer rather than recalculated silently later.
- Snapshot mode (#67): for releases bundled in this repository, the running app evaluates Fresh Tomato Score at the release's snapshot time (`created_at_utc`), not the wall clock, and the UI, `/status` and exports state the snapshot date and that the knowledge is not kept current. Other releases keep wall-clock freshness. See [source governance](source-governance.md#snapshot-mode-2026-10-07).

## Still Open

The release-qualified MVP is implemented and verified for its documented scope.
Remaining decisions and follow-up work are:

- Additional local provider adapters and supported generation/embedding models beyond the Ollama baseline.
- Retrieval or reranking changes that require a new evaluated baseline and compatible re-index.
- Broader native Linux and macOS environment qualification.
- Application-code packaging and update distribution, which remain separate from knowledge-release updates.
- The post-publication second review required by the single-maintainer fallback.
- Future product expansion beyond the current information-assistant boundary, including any personal-profile features.
