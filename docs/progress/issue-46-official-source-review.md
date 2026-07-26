# Issue 46 official-source human review

**Recorded:** 2026-07-26T21:20:10Z

**Human curator/reviewer:** `ericleungDK`

**Status:** Five-source review complete; follow-on knowledge-release rebuild
required.

## Outcome

The completed source-review bundle contains exact official-page snapshots,
normalized visible-main-text extractions, retrieval metadata, artifact hashes,
offline review pages, and the hash-bound human decision record. The durable,
versioned evidence is:

```text
data/source_reviews/issue-46/
```

[`completed-review.json`](../../data/source_reviews/issue-46/completed-review.json)
is the admission summary. It binds machine manifest SHA-256
`30069b0fad0bfd1900c29a359b6ae494217b878e4176e9cef28de3838a00c134`
to human-decisions SHA-256
`afd78dd4942e0e9ca5b8782edab66c9e57b0e38af008238e354fdce9361b656d`.
The later machine-only 404 observation is separately bound by supplemental
manifest SHA-256
`d1236d98549ea1c05ecbca30b513230c151c748808c99c6455e4f028bc49258b`;
it is not represented as human-reviewed evidence.
The reusable candidate configuration is
[`config/issue-46-official-source-review.json`](../../config/issue-46-official-source-review.json),
which records a reason for inclusion, issue-backed in-scope evidence, and the
named initial owner for every source. The private Windows-friendly working copy
under
`review/issue-46-official-source-review/` remains ignored and is not the
authoritative evidence.

All five candidate official sources were approved for a follow-on rebuild:

| Source | Snapshot SHA-256 | Normalized SHA-256 | Materiality | Review notes | Interpretation risks |
| --- | --- | --- | --- | --- | --- |
| Permanent-residence language requirements | `cedc3526858d09626cee90236b9cb63e656426afddf7e6ed7d3d89b2d08cf186` | `02e7ab4dcf1cbc31f8ddcb8eb153fb78ab67b42237c2d25ab0c4813dfef7e2a8` | Material | Replacement URL approved; conservatively material because the URL changed and the page reports a post-fixture update. | None identified |
| Tests equivalent to Danish language test 2 | `8a7a21699e6639a7dea03f6d631706790fb9037c6858eeb900747c55a95d1e6c` | `bee3bb2f18b5c50633e2b83183d29df4074d30ec4864a1dd8c32d77f60651e91` | Non-material | Reviewed content matches the fixture summary and reports no post-fixture update. | None identified |
| Tests equivalent to Danish language test 3 | `fd4271c42a66831270a201f4b4fc7b002851692154baf12d98f5220b54f8e18d` | `d29ca772e35ae460f8b6ea142b0cc21ef54a72666f352d225d9b93f058ade5fd` | Material | Conservatively material because the page reports a 07-07-2026 update after the fixture's 06-07-2026 check. | None identified |
| Danskprøver overview | `2f98bde5e598ba30bef1a60caba3b4933f42059a713a59391362ef5165ca807a` | `5c3024c8fd0825b2488a33d38d215f5f90d997f6331e2f75880fc2eca3632004` | Non-material | Reviewed content matches the fixture summary. | None identified |
| Registration deadlines and exam dates | `9bf530c74de8ec3e933074348dc45489ecfad8356c5f2d7d4ce0f50fa6710ba6` | `4e17cb33c05a025f764ebb626232ec12cadfa54eb420ca93a6a6bcd8032bcbab` | Non-material | Reviewed 2026 dates and registration information match the fixture summary. | None identified |

The human review recorded:

- curator, monitoring owner, and reviewer ID `ericleungDK`;
- approval of every candidate URL, publisher, topic, language, snapshot, and
  normalized extraction;
- no identified interpretation risks;
- the MVP single-maintainer fallback for the two material sources, with the
  required post-publication second review recorded as pending publication.

## Explicit issue resolutions

### Permanent-residence URL

The registry URL
`https://www.nyidanmark.dk/da/Du-vil-ansoege/Permanent-ophold` returned HTTP
404 with no redirects. Its 13,934-byte response was archived at
2026-07-26T21:32:15Z with SHA-256
`972d22107a1b673974a700b26920d9b18635650e4372ddf855cdee50f94ae68b`.
This objective check postdates the 21:20:10Z human review and is therefore
recorded as `post-review-machine-observation-not-human-review` in
[`supplemental-observations.json`](../../data/source_reviews/issue-46/supplemental-observations.json),
outside the human-bound machine manifest.
The curator approved this working official replacement:

```text
https://www.nyidanmark.dk/da/Du-vil-ans%C3%B8ge/Permanent-ophold/Permanent-ophold
```

The replacement returned HTTP 200 and visibly reported `07-07-2026` as its
source date and `Udlændingestyrelsen` as publisher.

### PD3 update

The PD3-equivalence page returned HTTP 200 and visibly reported a
`07-07-2026` update, after the fixture's recorded `06-07-2026` check. The
reviewer approved the snapshot and normalized extraction and conservatively
classified the change as material.

## Offline review behavior

The two Dansk og Prøver responses contain an inline rule that keeps the HTML
element invisible until live-site JavaScript loads. Exact snapshots therefore
appear blank when opened as local files. The generator preserves those source
bytes unchanged and creates separate offline review pages that:

- override only the hidden-page presentation state;
- block external scripts and resources with a content security policy;
- display a warning that the page is a derived review aid; and
- bind the review aid to the exact source-snapshot SHA-256 digest.

Derived review pages never count as official-source evidence.

## Reproducible workflow

The fetch step intentionally writes a private, unreviewed machine bundle
outside the repository:

```bash
python -m danish_rag.source_review \
  --repo-root . \
  --config config/issue-46-official-source-review.json \
  --output /tmp/issue-46-official-source-review \
  --retrieved-at-utc <UTC_TIMESTAMP>
```

After human decisions are recorded and hash-bound, the finalizer validates the
identities, artifact hashes, URL resolutions, materiality, staffing fallback,
review chronology, explicit fixture-evidence bans, and packet G boundary before
creating a durable bundle:

```bash
python -m danish_rag.source_review_finalize \
  --machine-bundle /tmp/issue-46-official-source-review \
  --decisions <HUMAN_DECISIONS_JSON> \
  --output data/source_reviews/issue-46
```

The `.gitattributes` rule for `data/source_reviews/**` disables line-ending
conversion so the exact evidence hashes remain stable on Windows checkouts.

## Boundaries and next step

Canonical packet G and `kr-2026-07-06.1` remain unchanged. This review does not
relabel packet G, qualify the project-authored fixture, or claim that its hashes
cover the reviewed official content.

The next maintainer must use the completed bundle to populate a new production
source registry, build normalized corpus documents from these reviewed
extractions, assemble and sign a new knowledge release, and run a new exact live
evaluation. The new release must not reuse packet G as qualification evidence.
