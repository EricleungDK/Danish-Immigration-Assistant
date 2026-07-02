# Danish Immigration RAG Architecture

This document records settled architecture plus project-level direction for Danish Immigration RAG. It is a decision summary, not an implementation plan; unresolved choices remain explicitly open.

## Scope And Traceability

- Runtime-baseline decisions proven by issue #26 are limited to the local provider baseline, generation/embedding capability separation, loopback defaults, release-network boundary, process/distribution baseline, first verified environment, and live structured-output probe. The traceable sources are [GitHub issue #26](https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/26), [docs/runtime-baseline.md](runtime-baseline.md), [docs/progress/issue-26-runtime-baseline.md](progress/issue-26-runtime-baseline.md), and the runtime sections of [.agent/issues/prd-runtime-and-retrieval-baseline.md](../.agent/issues/prd-runtime-and-retrieval-baseline.md).
- The interaction model, retrieval architecture, source governance, answer pipeline, and trust-indicator sections below preserve project-level context and pre-existing direction. They are not issue #26 completion claims unless an item explicitly cites the issue #26 runtime baseline.
- Retrieval-library choice, production chunking/ranking, supported embedding models, citation validation, answer schema, trust-scoring algorithms, and source-release governance remain deferred until their own benchmark or architecture gates approve them.

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
- `embeddinggemma` is a provisional embedding candidate only under issue #26. It is not a supported embedding model until retrieval benchmark evidence and later human architecture approval accept it.
- Each index is expected to record its embedding model and vector dimensions. Changing the embedding model is expected to require re-indexing. The production index implementation remains deferred.

## Local Data And Retrieval

This section preserves project-level direction for later retrieval work. Issue #26 did not select production retrieval libraries, chunking, ranking, reranking, or supported embedding models.

- Conversation history persists on the user's local disk.
- SQLite is the working store for conversations, messages, citations, model identity, corpus version, Evidence Confidence, and Fresh Tomato Score.
- MVP storage relies on per-user operating-system file permissions rather than application-level encryption.
- The intended retrieval direction is hybrid: semantic similarity, full-text matching, metadata filters, and combined ranking. The final production retrieval design requires benchmark evidence and architecture approval.
- Corpus installations contain normalized documents and metadata, not a provider-specific prebuilt vector index.
- New and changed chunks are expected to be embedded locally. Corpus installation should show progress and preserve the previous usable corpus and index if re-indexing fails; exact mechanics remain deferred.

## Source Governance And Updates

This section is project-level source governance direction. Issue #26 approved the runtime boundary between answer-time local operation and permitted release-network activity, but it did not complete production source-review workflow, release signing, or maintainer-role decisions.

- Project maintainers own a human-reviewed source registry rather than allowing each installation to define trust independently.
- Maintainer automation may fetch approved URLs and detect changes, but changed content requires review before publication.
- GitHub Releases is the initial authority for versioned knowledge releases.
- The application checks automatically for a newer knowledge release but requires explicit user approval before installation.
- Knowledge releases and application-code releases are independent. The application must not run `git pull` as an update mechanism.
- A knowledge release includes normalized documents, source URLs, check timestamps, content hashes, review status, corpus schema version, and minimum compatible application version.
- Release integrity is verified before installation, and installation is atomic with rollback on failure.

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
- The initial supported embedding models after retrieval benchmark approval
- Libraries and storage layout for vector indexing and full-text search
- Corpus chunking, ranking, and reranking strategies
- Source-review workflow, release signing, and maintainer roles
- Detailed browser security and local process lifecycle
- Application-code installation and update mechanism
- Evaluation datasets, acceptance thresholds, and hardware support targets
