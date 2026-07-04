# Issue 29 Hybrid Retrieval Comparison Progress

GitHub issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/29

Parent benchmark issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/3

Implementation source PRD: [.agent/issues/prd-runtime-and-retrieval-baseline.md](../../.agent/issues/prd-runtime-and-retrieval-baseline.md)

## Trace

- Corpus fixtures: [data/retrieval_benchmark/corpus-fixtures.json](../../data/retrieval_benchmark/corpus-fixtures.json)
- Lexical evaluation queries: [data/retrieval_benchmark/evaluation-queries.json](../../data/retrieval_benchmark/evaluation-queries.json)
- Dense evaluation queries: [data/retrieval_benchmark/dense-evaluation-queries.json](../../data/retrieval_benchmark/dense-evaluation-queries.json)
- Benchmark runner: [danish_rag/retrieval_benchmark.py](../../danish_rag/retrieval_benchmark.py)
- Unit tests: [tests/test_retrieval_benchmark.py](../../tests/test_retrieval_benchmark.py)
- Machine-readable comparison evidence: [docs/progress/issue-29-hybrid-retrieval-comparison.json](issue-29-hybrid-retrieval-comparison.json)
- Human-readable recommendation: [docs/progress/issue-29-hybrid-retrieval-recommendation.md](issue-29-hybrid-retrieval-recommendation.md)

## Commands

```bash
python3 -m unittest tests.test_retrieval_benchmark -v
python3 -m unittest discover -s tests -v
python3 -m danish_rag.retrieval_benchmark --mode compare
```

## TDD Record

- Hybrid comparison tests cover deterministic reciprocal-rank fusion over lexical and dense rankings, stable output for identical inputs, generated JSON and recommendation files, compatibility metadata, and production-threshold scope.
- Rejection-path tests cover the case where hybrid fails the recommendation rule and the best evidenced alternative is selected with rejection reasons.
- Shared retrieval benchmark tests continue to cover fixture validation, eligibility filtering before credit, dense index compatibility, invalid vectors, runtime/configuration metadata, and warm retrieval latency.

## Generated Evidence

The JSON comparison and markdown recommendation are generated measurements. They feed the later human architecture approval gate; they do not approve production thresholds or a production retrieval architecture.

Latest generated hybrid comparison evidence currently records:

- Executed at UTC: `2026-07-04T12:32:17.894334+00:00`
- Benchmark id: `mvp-hybrid-retrieval-comparison-issue-29`
- Runtime baseline: `mvp-runtime-baseline-issue-26`
- Observed Ollama provider version: `0.30.6`
- Observed Python version: `3.12.3`
- Embedding model: `embeddinggemma`
- Embedding endpoint: `http://127.0.0.1:11434`
- Configuration: corpus `data/retrieval_benchmark/corpus-fixtures.json`, lexical queries `data/retrieval_benchmark/evaluation-queries.json`, dense queries `data/retrieval_benchmark/dense-evaluation-queries.json`, index mode `rebuild`, output `docs/progress/issue-29-hybrid-retrieval-comparison.json`, recommendation `docs/progress/issue-29-hybrid-retrieval-recommendation.md`, timeout `60.0` seconds
- Vector dimensions: `768`
- Corpus SHA-256: `51f5c891fd742c1f67f076ccd13d5381bdd7ec9d46b3fb436acf6eb4ad143679`
- Fusion algorithm: reciprocal-rank fusion with `k=60`
- Query count: `9`
- Lexical Recall@3: `0.777778`
- Dense Recall@3: `0.777778`
- Hybrid Recall@3: `0.777778`
- Lexical MRR: `0.777778`
- Dense MRR: `0.666667`
- Hybrid MRR: `0.777778`
- Hybrid blocked-source violations: `0`
- Hybrid forbidden-result violations: `0`
- Selected candidate: `hybrid`

## Recommendation

Use `hybrid` as the evidenced retrieval candidate to bring into the later human architecture approval gate. Hybrid was selected because it met the issue #29 rule: no blocked-source violations, no exact-term Recall@3 regression against lexical retrieval, no English-paraphrase or typo Recall@3 regression against the single-mode candidates, complete compatibility metadata, and runtime-baseline compatibility.

Production thresholds remain out of scope and must be decided by the later evaluation decision ticket.

## Remaining Limitations

- The comparison uses the reviewed benchmark fixture set only.
- The recommendation is benchmark evidence for later human architecture approval, not production architecture approval.
- Generated evidence should be refreshed after any result-schema, fixture, query, policy, or runtime-baseline change before issue #29 is closed.
