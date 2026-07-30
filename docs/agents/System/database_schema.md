# Local SQLite Schema

## Tables

### `conversations`

Local conversation header and legacy first-turn fields. `id` is the primary key;
title, question, normalized question, serialized answer/model identity, corpus
identity, creation/update timestamps, and nullable soft-deletion timestamp are
stored. New reads take answer provenance from `conversation_turns`.

### `conversation_turns`

Append-only answer turns keyed by `id`, with `conversation_id`, unique
`turn_index`, question, normalized question, serialized structured answer,
serialized provider/model identity, corpus identity, and answer timestamp. The
serialized answer includes citations and the historical Evidence Confidence and
Fresh Tomato Score shown when the record is reopened.

### Retrieval `documents`

Per-active-release FTS backing table containing one retrieval unit per row plus
its source ID, title, publisher, official URL, language, topic tags,
review/health states, check time, content, and canonical JSON. A corpus-schema
`1.0` row is a whole document. A corpus-schema `2.0` row is a
`semantic-chunk-v1` unit whose document ID is its stable chunk ID; its canonical
JSON also retains the source-document ID and content identities. The table is a
derived index, not provenance authority.

### Retrieval `documents_fts`

SQLite FTS5 virtual table over document ID and indexed content using the
`unicode61` tokenizer. Dense vectors and index identity metadata are adjacent
derived artifacts tied to corpus/model/vector/schema identity.

## Relationships

- `conversation_turns.conversation_id` references `conversations.id`.
- `(conversation_id, turn_index)` is unique and preserves conversational order.
- Citations are embedded in each immutable answer JSON and reference the corpus
  identity stored on that same turn; they are not recomputed from the current
  corpus. Chunk citations additionally persist chunk/source-document IDs,
  source and normalized-content hashes, review state, source check time, and
  knowledge-release identity.
- Retrieval tables are rebuilt for one verified knowledge release and never mix
  documents across corpus identities.

## Indexes

- `idx_conversation_turns_conversation(conversation_id, turn_index)` supports
  ordered history reads.
- `documents_fts` provides lexical retrieval; local dense vector files provide
  semantic retrieval. RRF combines results after policy filtering.

## Migration History

`ConversationStore._ensure_schema` creates current tables idempotently. Its
legacy migration copies a pre-turn conversation into turn 1 only when no turn
exists. Knowledge-release indexes are disposable derived data and are rebuilt
when corpus, corpus/content-unit schema, embedding model identity, vector
dimensions, or index schema changes. Whole-document and chunked indexes use
distinct schema identities and are never mixed.

Implementation: [`danish_rag/conversation_store.py`](../../../danish_rag/conversation_store.py)
and [`danish_rag/retrieval.py`](../../../danish_rag/retrieval.py).
Completion evidence: [`../Reports/2026-07-14-mvp-completion-candidate.md`](../Reports/2026-07-14-mvp-completion-candidate.md).
