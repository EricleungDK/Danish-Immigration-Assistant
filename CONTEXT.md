# Danish Immigration RAG

Danish Immigration RAG is a private, source-grounded assistant for people seeking practical information about Danish permanent-residence language requirements and Danish language examinations. Architectural decisions using this language are recorded in [docs/architecture.md](docs/architecture.md).

## Product Boundary

**Danish Immigration RAG**:
A local application that answers questions from a curated corpus of approved official Danish sources. It is an information assistant, not an authority, lawyer, or personal eligibility assessor.
_Avoid_: Danish Immigration Assistant, RAG agent, legal assistant

**Local-only answer path**:
The boundary in which questions, retrieved evidence, model inference, answers, and conversation history remain on the user's computer.
_Avoid_: Local storage, private mode, offline mode

**Conversation record**:
A locally retained conversation containing the user's messages, generated answers, citations, model identity, corpus version, and trust indicators.
_Avoid_: User profile, cloud history

## Sources And Updates

**Approved official source**:
An authoritative Danish public source that has been admitted to the source registry through human review and may support factual answers.
_Avoid_: Search result, web source, reference material

**Source registry**:
The reviewed catalogue of approved official sources, their ownership, topic, language, and monitoring metadata.
_Avoid_: URL list, bookmarks

**Corpus**:
The versioned collection of normalized approved official sources installed on the user's computer and available for retrieval.
_Avoid_: Knowledge base, scraped web, live web

**Knowledge release**:
A reviewed, versioned publication of corpus documents and source metadata that users may install locally.
_Avoid_: Git pull, live crawl, code update

**Material source**:
An approved official source whose evidence is necessary to support a substantive claim in an answer.
_Avoid_: Related link, further reading

## Answers And Trust

**Official fact**:
A claim stated directly by an approved official source and presented without added inference.
_Avoid_: Advice, recommendation

**Interpretation**:
An explanation that connects or clarifies official facts without presenting the explanation as an official statement.
_Avoid_: Official guidance, legal conclusion

**Evidence confidence**:
A user-visible assessment of how directly and consistently approved official sources support an answer. It is not the generation model's confidence in its own output.
_Avoid_: Model confidence, accuracy score, certainty

**Fresh Tomato Score**:
A user-visible assessment of how recently material sources were checked and whether their approved content remains unchanged and healthy. An answer inherits the lowest score among its material sources.
_Avoid_: Evidence confidence, model confidence, freshness confidence

**Evidence-bounded answer**:
An answer limited to claims supported by retrieved approved official sources; unsupported portions are explicitly declined.
_Avoid_: Best-effort answer, model-knowledge answer

## Models

**Generation model**:
The user-selected local model that composes an answer from retrieved evidence. It is not itself a factual source.
_Avoid_: Knowledge source, authority

**Supported embedding model**:
A local embedding model whose retrieval behavior has been evaluated for this corpus and is permitted to build a compatible index.
_Avoid_: Any embedding model, generation model
