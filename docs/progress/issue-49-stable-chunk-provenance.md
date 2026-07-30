# Issue #49 — Stable Chunk Provenance

**Date:** 2026-07-30
**Status:** Implemented and machine-tested

## Delivered Contract

- Corpus schema `1.0` remains the whole-document format and continues to build
  `hybrid-index-v1`.
- Corpus schema `2.0` requires `semantic-chunk-v1` content units and builds
  `hybrid-chunk-index-v1`.
- Release authoring divides reviewed normalized documents at deterministic
  paragraph and sentence boundaries. A chunk ID binds the approved source ID,
  source-document ID, chunk content hash, and duplicate-content occurrence.
- Authoring recomputes the normalized document hash from supplied content, and
  signed-release verification reconstructs each ordered chunk sequence before
  accepting its reviewed normalized-content identity.
- Signed-release validation rejects unknown corpus schemas, undeclared chunk
  content units, missing chunk provenance, content/hash drift, forged chunk
  identities, duplicate retrieval-unit IDs, and source-provenance drift.
- Retrieved chunks retain the approved source identity, publisher, official
  URL, review state, source check time, source and normalized-content hashes,
  corpus identity, and knowledge-release identity.
- Material citations persist that chunk/source provenance inside the immutable
  conversation answer JSON.
- Chunked installation uses the existing staged build and atomic activation
  boundary. A simulated chunk embedding failure leaves the prior
  whole-document corpus/index pair active and queryable.
- The already-active shortcut checks both index metadata copies against the
  corpus, release, schema/content-unit, indexed-unit, and embedding identities;
  a stale whole-document identity is rebuilt instead of reported as active.

## Controlled End-to-End Case

The issue test fixture authors and signs a schema `2.0` release from one reviewed
source, installs it, builds the local lexical and dense chunk indexes, retrieves
the relevant chunk, validates a chunk citation through the full answer pipeline,
and reopens the saved conversation record with its original provenance.

## Verification

Focused issue and regression checks:

```text
53 tests passed
```

The focused set covers issue #49 plus release authoring, signed verification,
knowledge updates, atomic installation, hybrid retrieval, answer validation,
and conversation persistence.

Full Python verification:

```text
288 tests passed; 2 opt-in live-provider tests skipped
```

The full Playwright run passed 29 tests and skipped the opt-in live Ollama test.
`eval-016-keyboard-evidence-drawer` collided with the existing scheduled,
content-free `/knowledge-updates/automatic-check-status` request while asserting
that the drawer itself issues no request. The same workflow passed when rerun in
isolation. No issue #49 code changes the browser or automatic-update path.

The repository does not configure a Python type checker or an npm `typecheck`
script. Python modules were imported and executed by the focused tests with
bytecode writes disabled.
