# Danish Immigration RAG

Private, source-grounded local assistant for Danish permanent-residence language requirements and Danish language examinations.

## Runtime Baseline

- Runtime policy: [config/runtime-policy.json](config/runtime-policy.json)
- Human-readable baseline: [docs/runtime-baseline.md](docs/runtime-baseline.md)
- Issue #26 progress: [docs/progress/issue-26-runtime-baseline.md](docs/progress/issue-26-runtime-baseline.md)

Run the live local provider gate:

```bash
python3 -m danish_rag.runtime_probe --policy config/runtime-policy.json --evidence docs/progress/issue-26-runtime-probe.json
```
