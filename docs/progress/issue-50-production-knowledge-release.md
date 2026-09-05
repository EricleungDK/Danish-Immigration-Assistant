# Issue #50 — Reviewed production knowledge release

**Date:** 2026-09-05
**Status:** Implementation candidate machine-tested; production artifact pending
the existing project private signing key

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
| Versioned, signed, compatible, installed, indexed; no packet G reuse | Test candidate is versioned, test-signed, verified, qualified before activation, installed, and indexed; packet G is not used as qualification evidence | Production signing pending |
| Zero blocked/forbidden retrieval violations | Five installed-candidate cases report zero blocked, forbidden, or required-source misses | Test candidate passed; production run pending |
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

## Production signing gate

The repository intentionally contains only
`config/trust_roots/project-release-key-v1.json`, the public trust root. No
matching private key or configured private-key path is present. The test suite
uses an explicitly test-only isolated Ed25519 key and does not represent that
candidate as production-signed.

After the existing private key is made available from its durable
off-repository custody, the release operator can build the real candidate
without placing the key in the repository:

```bash
.venv/bin/python -B -m danish_rag.production_knowledge_release \
  --review-dir data/source_reviews/issue-46 \
  --registry-path data/source_registry/sr-2026-09-05.1.json \
  --release-dir data/knowledge_releases/kr-2026-09-05.1 \
  --source-registry-version sr-2026-09-05.1 \
  --release-id kr-2026-09-05.1 \
  --created-at-utc 2026-09-05T12:00:00Z \
  --next-review-due-utc 2026-10-26T20:55:12Z \
  --release-operator ericleungDK \
  --release-approver ericleungDK \
  --recovery-owner ericleungDK \
  --signing-private-key /secure/off-repository/project-release-key-v1.pem \
  --trust-root config/trust_roots/project-release-key-v1.json \
  --install-data-dir /secure/local-production-qualification-data \
  --embedding-model embeddinggemma \
  --embedding-endpoint http://127.0.0.1:11434
```

The production artifact, local production-key verification/install report, and
GitHub issue closure must remain pending until that command is run with the
real key. Canonical packet G and `kr-2026-07-06.1` remain unchanged and are not
used as qualification evidence for this candidate.
