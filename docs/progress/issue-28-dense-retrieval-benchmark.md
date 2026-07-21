# Issue 28 Dense Retrieval Benchmark Progress

GitHub issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/28

Parent benchmark issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/3

Implementation source PRD: [.agent/issues/prd-runtime-and-retrieval-baseline.md](../../.agent/issues/prd-runtime-and-retrieval-baseline.md)

## Trace

- Corpus fixtures: [data/retrieval_benchmark/corpus-fixtures.json](../../data/retrieval_benchmark/corpus-fixtures.json)
- Dense evaluation queries: [data/retrieval_benchmark/dense-evaluation-queries.json](../../data/retrieval_benchmark/dense-evaluation-queries.json)
- Benchmark runner: [danish_rag/retrieval_benchmark.py](../../danish_rag/retrieval_benchmark.py)
- Unit tests: [tests/test_retrieval_benchmark.py](../../tests/test_retrieval_benchmark.py)
- Opt-in live gate: [tests/test_dense_retrieval_benchmark_live.py](../../tests/test_dense_retrieval_benchmark_live.py)
- Generated dense index evidence: [docs/progress/issue-28-dense-index.json](issue-28-dense-index.json)
- Generated dense benchmark evidence: [docs/progress/issue-28-dense-retrieval-benchmark.json](issue-28-dense-retrieval-benchmark.json)

## Commands

```bash
python3 -m unittest tests.test_retrieval_benchmark -v
python3 -m unittest discover -s tests -v
python3 -m danish_rag.retrieval_benchmark --mode dense
DI_RAG_RUN_LIVE_DENSE_BENCHMARK=1 python3 -m unittest tests.test_dense_retrieval_benchmark_live -v
```

## TDD Record

- Dense runner tests cover embedding metadata, vector dimensions, dense index compatibility, metadata eligibility before credit, invalid vector diagnostics, runtime/configuration metadata, and warm retrieval latency separated from observable cold load.
- The opt-in live gate exercises the public dense benchmark seam against the configured local embedding endpoint and verifies that a rebuilt index can be reused.
- Normal unit discovery skips the live gate unless `DI_RAG_RUN_LIVE_DENSE_BENCHMARK=1` is set.

## Generated Evidence

The JSON files are generated measurements rather than approval records. The separate issue #4 approval comment dated 2026-07-05 approves `embeddinggemma` as the initial supported model after this evidence.

Latest generated dense benchmark evidence currently records:

- Executed at UTC: `2026-07-07T21:13:03.122064+00:00`
- Benchmark id: `mvp-dense-retrieval-benchmark-issue-28`
- Embedding model: `embeddinggemma`
- Embedding endpoint: `http://127.0.0.1:11434`
- Ollama provider version: `0.30.6`
- Configuration: corpus `data/retrieval_benchmark/corpus-fixtures.json`, queries `data/retrieval_benchmark/dense-evaluation-queries.json`, policy `config/runtime-policy.json`, index mode `rebuild`, timeout `60.0` seconds
- Vector dimensions: `768`
- Dense index size: `151360` bytes
- Dense indexing wall time: `1364.947` ms
- Embedding calls: `12`
- Process peak resident memory: `98.785` MB
- Query count: `3`
- Required-evidence query count: `2`
- Recall@1: `0.5`
- Recall@3: `1.0`
- Mean reciprocal rank: `0.75`
- Blocked-source violations: `0`
- Forbidden-result violations: `0`
- Mean query latency: `132.741` ms
- Mean warm retrieval latency: `59.086` ms
- Mean embedding load duration: `73.655` ms

## Recommendation

Issue #28 is an embedding-behavior benchmark slice. It records dense retrieval behavior and index compatibility evidence only. It did not itself approve the architecture or model; the later issue #4 approval comment dated 2026-07-05 approved `embeddinggemma` using the completed issue #29 comparison. This benchmark still does not set production thresholds.

Hybrid comparison and the human-readable retrieval recommendation are owned by issue #29.

## Remaining Limitations

- Dense retrieval quality is measured on the reviewed issue #28 fixture set only.
- Generated evidence should be refreshed after any result-schema or fixture change before issue #28 is closed.
