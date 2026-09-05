# Issue #50 — Reviewed production knowledge release

**Date:** 2026-09-05
**Status:** Signing key reset; reviewed candidate signed, live-qualified, and
installed locally as `kr-2026-09-05.1` (52 semantic chunks)

## Delivered contract

`danish_rag.production_knowledge_release` builds a new candidate only from the
completed issue-46 review bundle. The builder:

- verifies the exact machine-review and human-decision file hashes recorded by
  `completed-review.json`;
- rebuilds the canonical completed-review record from the bound machine bundle,
  supplemental observations, and human decisions, rejecting schema, named
  identity, timestamp, staffing, URL-provenance, or packet-G contract drift;
- pins each source to its configured official publisher and requires
  supplemental replacement-URL evidence labeled post-review to actually
  postdate the human review;
- cross-checks every flattened completed-review decision against the bound
  `human-decisions.json` record;
- verifies every archived snapshot and normalized extraction against all three
  copies of its reviewed SHA-256 identity, parsing the same bytes that were
  hashed so a concurrent file change cannot escape the binding;
- requires the five configured source identities, completed curator admission,
  completed human review, and the human-approved current or replacement URL;
- writes a production source registry naming the curator, reviewer, monitoring
  owner, release operator, release approver, recovery owner, materiality, and
  the approved MVP single-maintainer fallback;
- derives the corpus only from the reviewed official-source extraction, records
  both the exact extraction hash and the derived normalized-document hash, and
  creates deterministic schema-2 semantic chunks;
- rejects a candidate timestamp earlier than any bound curation, review, or
  retrieval evidence timestamp;
- exclusively claims candidate output paths, removes partial outputs after a
  failed build, signs and immediately verifies the release with a caller-supplied
  Ed25519 private key and application trust root; and
- cross-checks the registry, signed manifest, and every released chunk before
  returning a candidate.

The installed-candidate verifier copies the candidate once, builds isolated
local lexical and dense chunk indexes from that pinned copy, and runs the fixed
five production-source retrieval cases. It rejects substituted or weakened
query suites and fails on a missing required source, a blocked source, or a
forbidden source-document result before installing that exact qualified copy in
the target data directory.
The rollback verifier injects failures during verification, extraction,
embedding, indexing, and activation and proves that the prior reviewed release
remains active and queryable. Failed installs also remove their private staging
directory.

Production chunks now retain the exact reviewed-extraction digest in retrieval
results and persisted citations. Update discovery also treats a change to that
digest as a reviewed-source change. Retrieval accepts the two reviewed source
languages (`da` and `en-GB`).

## Acceptance-criterion evidence

| Issue #50 criterion | Evidence | Status |
| --- | --- | --- |
| Review and approved-URL binding | Exact review/bundle/decision/snapshot/extraction checks plus registry-to-release cross-check | Machine-tested |
| Five deterministic sources with provenance | Two independent builds produce identical chunk IDs; all five sources appear | Machine-tested |
| Named registry roles, materiality, and fallback | Qualified generated registry assertions for all five sources and both material sources | Machine-tested |
| Versioned, signed, compatible, installed, indexed; no packet G reuse | Production candidate is versioned, v2-signed, verified, live-qualified before activation, installed, and indexed; packet G is not used as qualification evidence | Production-key signed and locally installed |
| Zero blocked/forbidden retrieval violations | Five installed-candidate cases report zero blocked, forbidden, or required-source misses | Live candidate passed all five cases |
| Prior release retained on failure | Five-stage candidate rollback matrix | Machine-tested |

## Integration and validation (2026-09-05)

Recovered the existing implementation through `fed6035` from GitHub and
integrated it on the current `main` branch, starting at `5ada1c9`. This includes
the prerequisite issue #49 trust, provenance, and atomic-activation fixes.

Fresh independent Standards and Spec reviews found two issues, now resolved:

- Installation now verifies the private copied manifest, signature, and exact
  corpus bytes before indexing or activation. A regression mutates each input
  after initial verification and proves rejection, prior-release queryability,
  and staging cleanup.
- Successful dense-index derivation checks are cached in memory by the exact
  search texts, vectors, model/provider metadata, and endpoint. The cache holds
  at most eight digests and never persists evidence. Reopening unchanged indexes
  avoids corpus embedding; changed inputs still require derivation validation.

Three older synthetic retrieval/answer tests depended on the wall clock and
failed after their fixture review period expired. Their tests now pin source
freshness to 2026-07-30 without changing production policy or signed fixtures.
Browser verification also exposed two test synchronization races: the conversation
helper now waits for a new turn, and the drawer test waits for the independent
page-load update check to finish before measuring requests. The assertions remain
unchanged; the final review found no test weakening.

- Issue #50 plus chunk installation/validation regression suite: `63 passed`.
- Full Python suite: `353 run`, `351 passed`, `2` opt-in live-provider skips.
- Browser suite: `30 passed`, `1` opt-in live-Ollama skip.
- Ruff baseline correctness checks (`--isolated --select E4,E7,E9,F`) on changed
  Python files and whitespace checks: passed. Ruff 0.16.6's broader default
  rules also report existing style/refactoring findings; no repository lint
  configuration is present, and those broad findings were not swept into this
  change.
- Parse/import smoke checks on changed production modules: passed. The
  repository has no configured static type checker.
- Final independent reviews: Standards has no remaining actionable findings;
  Spec has no remaining code correctness findings, with the production signing
  and installation evidence still pending the existing key.

These are machine/test-candidate results, not production release qualification.

## Signing-key reset and live candidate (2026-09-05)

The owner authorized a reset after confirming that custody of the original
private key could not be established. The new `project-release-key-v2` private
key resides at:

```text
/home/ericl/.local/share/danish-immigration-rag-signing/project-release-key-v2.pem
```

The directory is owner-only (`0700`), and the file is owner-readable/writable
(`0600`). Only the public key is committed. The custody/fingerprint record is
[`issue-50-signing-key-reset.json`](issue-50-signing-key-reset.json).
An off-device owner-controlled backup remains pending before public publication.

The v1 trust root is retired and restricted to the exact existing fixture
manifest digest. Its original signature is still verified. Neither fixture
bytes nor canonical packet G were changed; v1 cannot authorize new manifests.

The real `embeddinggemma` qualification initially found one required-source
miss: multiple chunks from the equivalence pages occupied all three result
slots, excluding the primary permanent-residence source. Schema-2 final result
selection now reserves detected intents, then prefers distinct eligible sources,
then fills remaining slots with repeat-source chunks. Selected chunks retain
fused rank order, and schema-1 selection is unchanged. A deterministic regression
reproduces the ranking pressure. The original five-case query suite and its
thresholds were unchanged.

The signed candidate contains all five reviewed sources and 52 deterministic
semantic chunks. It passed live qualification with zero blocked-source,
forbidden-result, or required-source violations and was atomically installed at
`/home/ericl/.local/share/danish-immigration-rag`.

Artifacts and evidence:

- [`kr-2026-09-05.1/manifest.json`](../../data/knowledge_releases/kr-2026-09-05.1/manifest.json)
  and its detached signature bind the exact reviewed corpus artifact.
- [`sr-2026-09-05.1.json`](../../data/source_registry/sr-2026-09-05.1.json)
  records source admissions, review/monitor provenance, named roles, and fallback.
- [`issue-50-production-install.json`](issue-50-production-install.json)
  records the five-case live retrieval results from the pinned candidate.
- [`issue-50-production-rollback.json`](issue-50-production-rollback.json)
  records live-provider fault injection at verification, extraction, embedding,
  indexing, and activation. The old fixture is only a rollback control; packet G
  is not used as candidate qualification evidence.

Reset verification: `356` Python tests ran (`354 passed`, `2` opt-in skips);
Playwright passed `30` tests with `1` opt-in live-Ollama skip. The live five-stage
rollback matrix passed. Baseline Ruff and parse/import/whitespace checks passed.
Both final independent review axes reported no findings. Signed knowledge-release
files are marked `-text` in `.gitattributes` to preserve exact bytes across
Windows and Linux checkouts.

No GitHub release was published. This candidate does not satisfy the separate
final-answer human adjudication, accessibility, environment, post-publication
second review, or public-release approval gates.

To verify the committed release without accessing the private key:

```bash
.venv/bin/python -B -c "from danish_rag.knowledge_release import verify_knowledge_release; verify_knowledge_release('data/knowledge_releases/kr-2026-09-05.1')"
```

For a future rebuild, use a new release ID and output directory with the
`danish_rag.production_knowledge_release` command, supplying the private-key path
above and `--trust-root config/trust_roots/project-release-key-v2.json`.
Existing candidate outputs are deliberately not overwritten.
