# Source Registry Evidence: `sr-2026-07-06.1`

## Outcome

The active fixture corpus now has an explicit, machine-readable source-registry
record. Its production qualification is **blocked**; no production human source
review is claimed.

## Audit Findings

- Registry sources: 5
- Bundled corpus documents: 5
- Document origin: 5 `project-authored-fixture`
- Production human-reviewed sources recorded: 0
- Official-source snapshot hashes recorded: 0
- Curator admission records: 0
- Source monitoring owner/fetch records: 0
- Production-release-eligible sources: 0

The bundled manifest calls each source `approved-current` and retains the
`mvp-fixture-reviewer` label for fixture behavior. For every source, both
provenance-named manifest hashes equal the SHA-256 hash of the corresponding
project-authored summary. Those values are not treated as evidence of an
archived official-page snapshot or an independently reviewed normalized
extraction.

## Evidence And Enforcement

- Registry artifact:
  [`data/source_registry/sr-2026-07-06.1.json`](../../data/source_registry/sr-2026-07-06.1.json)
- Loader and cross-release validator:
  [`danish_rag/source_registry.py`](../../danish_rag/source_registry.py)
- Contract tests:
  [`tests/test_source_registry.py`](../../tests/test_source_registry.py)

The validator derives these blocking reason codes from the evidence rather
than trusting the declared status:

- `official-source-snapshots-not-recorded`
- `fixture-governance-evidence-only`
- `production-human-source-review-not-recorded`
- `project-authored-fixture-content`
- `source-curation-not-recorded`
- `source-monitoring-evidence-not-recorded`

It also rejects production eligibility without a completed human review,
rejects the fixture reviewer label as human-review evidence, and detects drift
between the registry's fixture projection and the active release. Material
source-change evidence cannot pass with only one reviewer identity.

## Verification

Run:

```bash
.venv/bin/python -m unittest tests.test_source_registry -v
```

Result on 2026-07-14: 6 tests passed.

## Remaining External Review

Production qualification requires official-source snapshots and normalized
extracts for all five sources, named human review evidence for each comparison,
materiality and interpretation-risk decisions, applicable second-review or MVP
fallback evidence, and a rebuilt signed release with truthful provenance
hashes. None of those human decisions is represented by the fixture label.
