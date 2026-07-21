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

Per-active-release FTS backing table containing document/source IDs, title,
publisher, official URL, language, topic tags, review/health states, check time,
content, and canonical document JSON. It is a derived index, not provenance
authority.

### Retrieval `documents_fts`

SQLite FTS5 virtual table over document ID and indexed content using the
`unicode61` tokenizer. Dense vectors and index identity metadata are adjacent
derived artifacts tied to corpus/model/vector/schema identity.

## Relationships

- `conversation_turns.conversation_id` references `conversations.id`.
- `(conversation_id, turn_index)` is unique and preserves conversational order.
- Citations are embedded in each immutable answer JSON and reference the corpus
  identity stored on that same turn; they are not recomputed from the current
  corpus.
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
when corpus, embedding model identity, vector dimensions, or index schema changes.

Implementation: [`danish_rag/conversation_store.py`](../../../danish_rag/conversation_store.py)
and [`danish_rag/retrieval.py`](../../../danish_rag/retrieval.py).
Completion evidence: [`../Reports/2026-07-14-mvp-completion-candidate.md`](../Reports/2026-07-14-mvp-completion-candidate.md).
