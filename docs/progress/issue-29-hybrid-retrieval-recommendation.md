# Issue 29 Hybrid Retrieval Recommendation

Machine-readable result: `docs/progress/issue-29-hybrid-retrieval-comparison.json`
Executed at UTC: `2026-07-04T12:32:17.894334+00:00`
Selected candidate: hybrid

Production thresholds remain out of scope and must be decided by the later evaluation decision ticket.

## Candidate Metrics

### lexical

- Recall@1: `0.777778`
- Recall@3: `0.777778`
- Mean reciprocal rank: `0.777778`
- Blocked-source violations: `0`
- Forbidden-result violations: `0`
- Mean latency ms: `0.117`

### dense

- Recall@1: `0.555556`
- Recall@3: `0.777778`
- Mean reciprocal rank: `0.666667`
- Blocked-source violations: `0`
- Forbidden-result violations: `0`
- Mean latency ms: `128.253`

### hybrid

- Recall@1: `0.777778`
- Recall@3: `0.777778`
- Mean reciprocal rank: `0.777778`
- Blocked-source violations: `0`
- Forbidden-result violations: `0`
- Mean latency ms: `0.021`

## Non-Selected Alternatives

- No candidate was rejected by the issue 29 rule.

## Compatibility Metadata

- Runtime baseline: `mvp-runtime-baseline-issue-26`
- Embedding model: `embeddinggemma`
- Vector dimensions: `768`
- Corpus SHA-256: `51f5c891fd742c1f67f076ccd13d5381bdd7ec9d46b3fb436acf6eb4ad143679`
- RRF k: `60`

## Recommendation

Use `hybrid` as the evidenced retrieval candidate to bring into the later human architecture approval gate.
