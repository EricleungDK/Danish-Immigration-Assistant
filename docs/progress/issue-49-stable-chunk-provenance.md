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
- Whole-document schema `1.0` rejects chunk-shaped retrieval units instead of
  silently indexing them under `hybrid-index-v1`. Chunked schema `2.0` requires
  valid source and normalized-content SHA-256 identities plus a non-empty
  string source-document identity at both authoring and signed verification.
- Signed schema `2.0` verification also rejects blank or unsafe release/corpus
  identities, blank approved-source identities, invalid source-check times,
  and non-string or empty chunk content before staging or activation.
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
49 issue #49 tests passed
```

The focused set covers deterministic authoring, signed verification,
whole-document/chunked schema isolation, index compatibility, rollback,
retrieval provenance, answer validation, and conversation persistence.

Full Python verification:

```text
325 tests passed; 2 opt-in live-provider tests skipped
```

The final full Playwright run passed all 30 non-live tests and skipped the
opt-in live Ollama test. Earlier runs reproduced the pre-existing
`eval-016-keyboard-evidence-drawer` race, where the assertion observed the
scheduled, content-free `/knowledge-updates/automatic-check-status` request;
the exact workflow passed in isolation. No issue #49 code changes the browser
or automatic-update path.

The repository does not configure a Python type checker or an npm `typecheck`
script. Python modules were imported and executed by the focused tests with
bytecode writes disabled.

## Review Resolution

The first three-axis review reported no spec findings and three concrete
correctness risks. Regression tests now prove that schema `1.0` rejects
chunk-shaped documents, schema `2.0` rejects invalid source-content hashes, and
null source-document identities cannot pass either authoring or signed-release
verification.

The second review confirmed those fixes and found five more malformed signed
artifact cases. Regression tests now prove that blank release/corpus identities
cannot reach activation, blank source identities cannot produce source-less
citations, invalid check timestamps cannot become trust indicators, and
non-string chunk content cannot pass verification and fail later inside the
SQLite index build.

The third review confirmed the preceding fixes and found six final boundary
cases. Regression tests now require every approved manifest source to produce
chunks, reject duplicate source identities and malformed reviewer evidence,
reject non-string content before authoring, reject control characters in
release identities, and rebuild rather than crash when active dense-index
metadata is malformed.

The fourth review found five more signed-artifact and active-index integrity
boundaries. Regression tests now bind duplicate-content occurrence numbers to
canonical chunk order; reject control characters and surrounding whitespace in
source and source-document identities; reject empty chunk releases; and force
rebuilds for tampered corpus bytes, malformed dense vectors, changed signed
content under a reused release ID, or divergent lexical-index content.

The fifth review closed the remaining active-load and module-boundary gaps.
Normal retrieval now checks the signed corpus artifact identity before serving
content, new installations bind every derived index file to an activation-time
digest, and retrieval owns the complete vector/lexical compatibility check.
Canonical identities also reject lone Unicode surrogates with domain-specific
validation errors instead of leaking encoding exceptions.

The sixth review hardened the complete activation contract. Installed chunk
corpora retain the signed manifest and detached signature and reverify them on
load; derived vectors and lexical rows are recomputed against release content;
activation rolls back directories and the active pointer together; and
retrieval reads one locked active snapshot. Legacy whole-document indexes
without digest metadata remain usable only after their derived content passes
the same direct validation.

The seventh review removed the last mutable and lifetime-bound seams. Active
records no longer choose their trust root; custom roots are anchored outside
the active pointer, while production roots remain application-owned. Chunk
indexes require activation digests, interrupted same-ID swaps recover from a
transaction marker, and each retriever owns an in-memory lexical snapshot for
its full lifetime. Pre-upgrade whole-document records must match the bundled
signed release before their directly validated index can be used.

The standards review also noted that production maintainer-role evidence is not
part of the existing manifest contract. That gap predates issue #49, and the
source-governance contract already keeps fixture-built releases blocked from
production qualification. Changing the production governance manifest is a
separate contract change, not a chunk-provenance fix. The review's schema-switch
and provenance-dictionary smell heuristics remain intentional at the small
two-schema JSON boundary; authoring and verification independently recompute
content identities so one path cannot self-certify the other.
