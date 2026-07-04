# Danish Immigration RAG Architecture

This document records the architectural direction agreed during the initial design discussion. It is a decision summary, not an implementation plan; unresolved choices remain explicitly open.

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

- The primary interface is a calm, conversation-first experience rather than a source browser or research workbench.
- The desktop layout uses a narrow local-conversation sidebar and a flexible chat canvas. Official evidence opens in a slide-over drawer instead of permanently competing with the answer for width.
- The large product prompt is an empty state only. Active conversations use a compact title and status line plus a persistent multiline composer.
- Each exchange follows a conversational rhythm: user message, assistant identity and source status, natural-language answer, compact inline citations, optional support details, and suggested follow-up questions.
- The answer keeps only essential provenance visible: material-source count, Evidence Confidence, and source check date. Fresh Tomato Score explanations, corpus identity, freshness methodology, model identity, and update controls live in the evidence drawer.
- Official facts and interpretation remain distinguishable through restrained margin labels. Colored callouts are reserved for warnings and explicit evidence-bounded refusals.
- The approved direction was validated in the throwaway prototype at [`visualization/danish-rag-ui-prototype.html`](../visualization/danish-rag-ui-prototype.html). Prototype mechanics and styling are not production implementation requirements.

## Local Model Integration

- Users choose and run their own local model provider; Ollama is not mandatory.
- Provider-specific differences are isolated behind independent adapters rather than treated as perfectly interchangeable.
- Generation and embedding are separate capabilities and may use different providers or models.
- Provider selection is manual in the MVP and includes a connection test; automatic provider discovery is not required.
- Compatible local generation models remain configurable.
- Embedding models are restricted to a small evaluated set because retrieval quality must remain testable.
- Each index records its embedding model and vector dimensions. Changing the embedding model requires re-indexing.

## Local Data And Retrieval

- Conversation history persists on the user's local disk.
- SQLite is the working store for conversations, messages, citations, model identity, corpus version, Evidence Confidence, and Fresh Tomato Score.
- MVP storage relies on per-user operating-system file permissions rather than application-level encryption.
- Retrieval is hybrid: semantic similarity, full-text matching, metadata filters, and combined ranking.
- Corpus installations contain normalized documents and metadata, not a provider-specific prebuilt vector index.
- New and changed chunks are embedded locally. Corpus installation must show progress and preserve the previous usable corpus and index if re-indexing fails.

## Source Governance And Updates

- Project maintainers own a human-reviewed source registry rather than allowing each installation to define trust independently.
- Maintainer automation may fetch approved URLs and detect changes, but changed content requires review before publication.
- GitHub Releases is the initial authority for versioned knowledge releases.
- The application checks automatically for a newer knowledge release but requires explicit user approval before installation.
- Knowledge releases and application-code releases are independent. The application must not run `git pull` as an update mechanism.
- A knowledge release includes normalized documents, source URLs, check timestamps, content hashes, review status, corpus schema version, and minimum compatible application version.
- Release integrity is verified before installation, and installation is atomic with rollback on failure.

## Answer Pipeline

- The MVP uses a constrained RAG pipeline, not an autonomous agent loop.
- The pipeline normalizes the question, identifies ambiguity, retrieves approved evidence, rejects unsupported claims, generates a structured answer, validates citations, and stores the answer with its provenance.
- The model does not browse, choose arbitrary tools, or supply unsupported facts from its pretrained knowledge.
- When only part of a question is supported, the application answers that portion and explicitly declines the unsupported portion.
- Official facts and interpretation remain visibly distinct.

## Trust Indicators

- **Evidence Confidence** measures how directly and consistently retrieved approved sources support the answer. It is computed from evidence and citation coverage, not model self-rating.
- **Fresh Tomato Score** measures source recency and health independently of Evidence Confidence.
- Each source retains its own Fresh Tomato Score. The answer-level score is the lowest score among material sources.
- Trust indicators, their reasons, citations, model identity, and corpus version are stored with the historical answer rather than recalculated silently later.

## Still Open

- Exact provider adapter contracts and the first officially supported providers
- The initial supported embedding models
- Libraries and storage layout for vector indexing and full-text search
- Corpus chunking, ranking, and reranking strategies
- Source-review workflow, release signing, and maintainer roles
- Detailed browser security and local process lifecycle
- Application-code installation and update mechanism
- Evaluation datasets, acceptance thresholds, and hardware support targets
