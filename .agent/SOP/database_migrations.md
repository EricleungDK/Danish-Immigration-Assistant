# Local Database And Index Changes

## Purpose

Keep local conversation records forward-compatible and keep derived retrieval
indexes bound to their exact corpus and embedding identity.

## Prerequisites

- Read [the current schema](../System/database_schema.md).
- Preserve historical answer JSON, citations, provider/model identity, corpus
  identity, and trust indicators.
- Add a focused migration/re-index regression before changing persisted shape.

## Step-by-Step Instructions

1. Add idempotent SQLite schema creation or a narrowly scoped forward migration in
   `ConversationStore._ensure_schema`.
2. Never rewrite historical turn provenance from current model/corpus state.
3. For retrieval changes, bump the relevant index/schema compatibility value and
   rebuild derived data rather than coercing incompatible vectors.
4. Perform activation only after corpus copy, embedding, indexing, and identity
   checks succeed; preserve the prior queryable pair on every failure.
5. Update `.agent/System/database_schema.md`, architecture docs, and release
   evidence in the same change.

## Validation

Run conversation persistence, embedding re-index, atomic installation, and safe
recovery tests, followed by full unit discovery. Run the six-phase rollback
monitor for release-affecting changes.

## Troubleshooting

If an old record cannot be read, stop and add a forward migration; do not discard
it. If index identity differs, rebuild locally. If update activation fails, report
rollback and verify the prior corpus/index remains active and queryable.
