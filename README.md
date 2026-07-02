# Danish Immigration RAG

Private, source-grounded local assistant for Danish permanent-residence language requirements and Danish language examinations.

## Runtime Baseline

- Runtime policy: [config/runtime-policy.json](config/runtime-policy.json)
- Human-readable baseline: [docs/runtime-baseline.md](docs/runtime-baseline.md)
- Issue #26 progress: [docs/progress/issue-26-runtime-baseline.md](docs/progress/issue-26-runtime-baseline.md)
- Retrieval benchmark fixtures: [data/retrieval_benchmark/corpus-fixtures.json](data/retrieval_benchmark/corpus-fixtures.json)
- Retrieval benchmark queries: [data/retrieval_benchmark/evaluation-queries.json](data/retrieval_benchmark/evaluation-queries.json)
- Dense retrieval benchmark queries: [data/retrieval_benchmark/dense-evaluation-queries.json](data/retrieval_benchmark/dense-evaluation-queries.json)

Run the live local provider gate:

```bash
python3 -m danish_rag.runtime_probe --policy config/runtime-policy.json --evidence docs/progress/issue-26-runtime-probe.json
```

Run the local lexical retrieval benchmark:

```bash
python3 -m danish_rag.retrieval_benchmark --corpus data/retrieval_benchmark/corpus-fixtures.json --queries data/retrieval_benchmark/evaluation-queries.json --output docs/progress/issue-27-retrieval-benchmark.json
```

Run the local dense retrieval benchmark with the provisional embedding candidate:

```bash
python3 -m danish_rag.retrieval_benchmark --mode dense
```
