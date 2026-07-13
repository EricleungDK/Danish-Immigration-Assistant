# Danish Immigration RAG

Private, source-grounded local assistant for Danish permanent-residence language requirements and Danish language examinations.

## Project Baselines

- Runtime policy: [config/runtime-policy.json](config/runtime-policy.json)
- Human-readable baseline: [docs/runtime-baseline.md](docs/runtime-baseline.md)
- Source governance recommendation: [docs/source-governance.md](docs/source-governance.md)
- Evaluation quality bar candidate: [docs/evaluation-quality-bar.md](docs/evaluation-quality-bar.md)
- Issue #5 source governance progress: [docs/progress/issue-5-source-governance.md](docs/progress/issue-5-source-governance.md)
- Issue #6 source governance approval: [docs/progress/issue-6-source-governance-approval.md](docs/progress/issue-6-source-governance-approval.md)
- Issue #7 evaluation quality-bar progress: [docs/progress/issue-7-evaluation-quality-bar.md](docs/progress/issue-7-evaluation-quality-bar.md)
- Issue #8 local app setup progress: [docs/progress/issue-8-local-app-setup.md](docs/progress/issue-8-local-app-setup.md)
- Issue #23 accessibility and responsive review: [docs/progress/issue-23-accessibility-responsive.md](docs/progress/issue-23-accessibility-responsive.md)
- Issue #24 usability validation packet: [docs/progress/issue-24-usability-validation.md](docs/progress/issue-24-usability-validation.md)
- Issue #26 progress: [docs/progress/issue-26-runtime-baseline.md](docs/progress/issue-26-runtime-baseline.md)
- Issue #28 dense benchmark progress: [docs/progress/issue-28-dense-retrieval-benchmark.md](docs/progress/issue-28-dense-retrieval-benchmark.md)
- Issue #29 hybrid comparison progress: [docs/progress/issue-29-hybrid-retrieval-comparison.md](docs/progress/issue-29-hybrid-retrieval-comparison.md)
- Evaluation quality-bar config: [config/evaluation-quality-bar.json](config/evaluation-quality-bar.json)
- Evaluation set candidate: [data/evaluation/evaluation-set-v0.1-candidate.json](data/evaluation/evaluation-set-v0.1-candidate.json)
- Retrieval benchmark fixtures: [data/retrieval_benchmark/corpus-fixtures.json](data/retrieval_benchmark/corpus-fixtures.json)
- Retrieval benchmark queries: [data/retrieval_benchmark/evaluation-queries.json](data/retrieval_benchmark/evaluation-queries.json)
- Dense retrieval benchmark queries: [data/retrieval_benchmark/dense-evaluation-queries.json](data/retrieval_benchmark/dense-evaluation-queries.json)

## Portfolio Case Study

  ### Problem
  Official Danish immigration websites are written for the general public, not for someone trying to understand how the
  rules apply to their own situation. Immigration processes can also take months or years, so users need answers that stay
  connected to current official sources.

  ### User
  People preparing for Danish permanent residence, especially applicants who need to understand Danish language and exam
  requirements.

  ### Architecture
  The app runs locally as a private web assistant. It uses an approved knowledge release of official Danish immigration
  sources, builds a local retrieval index, retrieves relevant source passages for a user question, and then asks a local
  language model to generate an answer only from that evidence. The answer pipeline separates official facts,
  interpretation, refusals, citations, source freshness, and conversation history.

  ### Safety/Evals
  The project treats immigration guidance as a high-trust domain, so the app does not give legal advice or personal
  eligibility decisions. It checks that official facts have citations, blocks unsupported claims, refuses unsafe questions,
  and separates retrieval evaluation from final-answer evaluation. The test suite covers retrieval, citation validation,
  privacy boundaries, source governance, rollback behavior, conversation history, accessibility, and browser workflows.

  ### What I used AI for
  I used AI coding agents to help draft implementation code, tests, documentation, architecture notes, and issue-based
  development plans. I also plan to use AI-generated HTML explainers to study the system until I can explain the
  architecture and evaluation design myself.

  ### What I personally reviewed/owned
  I owned the project scope, user problem, issue tickets, acceptance criteria, product boundaries, documentation direction,
  test runs, and final portfolio story. I also reviewed whether the app’s behavior matched the safety goal: source-backed
  guidance, not legal authority.

  ### What I would improve next
  Create a guided personal-profile layer only after the current source-backed assistant is stable. This would require
  stronger privacy design, clearer consent, and stricter refusal behavior because personal memory increases safety risk.

### Local Application

Install the local web application dependencies in a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm install
```

Launch the single local Python process:

```bash
.venv/bin/python -m danish_rag.local_app
```

The default bind address is `127.0.0.1:8000`, from [config/runtime-policy.json](config/runtime-policy.json). The first-launch setup page stores validated provider configuration at the per-user config path and does not require Ollama; Ollama remains the first baseline provider option.

Run browser-level setup tests:

```bash
npm run test:browser
```

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

Run the opt-in live dense benchmark gate:

```bash
DI_RAG_RUN_LIVE_DENSE_BENCHMARK=1 python3 -m unittest tests.test_dense_retrieval_benchmark_live -v
```

Run the local hybrid retrieval comparison and recommendation:

```bash
python3 -m danish_rag.retrieval_benchmark --mode compare
```
