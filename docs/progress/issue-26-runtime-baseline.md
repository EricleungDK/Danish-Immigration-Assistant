# Issue 26 Runtime Baseline Progress

GitHub issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/26

Parent decision issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/2

Implementation source PRD: [.agent/issues/prd-runtime-and-retrieval-baseline.md](../../.agent/issues/prd-runtime-and-retrieval-baseline.md)

## Trace

- Machine-readable policy: [config/runtime-policy.json](../../config/runtime-policy.json)
- Human-readable baseline: [docs/runtime-baseline.md](../runtime-baseline.md)
- Runtime policy and probe code: [danish_rag/runtime_policy.py](../../danish_rag/runtime_policy.py), [danish_rag/runtime_probe.py](../../danish_rag/runtime_probe.py)
- Contract tests: [tests/test_runtime_policy_contract.py](../../tests/test_runtime_policy_contract.py)
- Probe tests: [tests/test_runtime_probe.py](../../tests/test_runtime_probe.py)
- Live evidence output: [docs/progress/issue-26-runtime-probe.json](issue-26-runtime-probe.json)

## TDD Record

- Red: `python3 -m unittest discover -s tests -v` failed because `danish_rag.runtime_policy` and `danish_rag.runtime_probe` did not exist.
- Green pass 1: policy/probe implementation made seven probe and policy tests pass while documentation tests still failed because `docs/runtime-baseline.md` was absent.
- Green pass 2: runtime documentation embedded the checked policy contract.

## Acceptance Criteria Mapping

- Machine-readable runtime policy identifies Ollama, version floor, `gemma4:12b`, `embeddinggemma`, and capability boundaries: `config/runtime-policy.json`.
- Contract tests detect policy and documentation drift: `tests/test_runtime_policy_contract.py`.
- Live runtime probe verifies service reachability, version, installed model identity, completion, and structured JSON: `danish_rag/runtime_probe.py`.
- Actionable diagnostics for missing service, missing model, incompatible version, and invalid structured output: `tests/test_runtime_probe.py`.
- Runtime documentation distinguishes the local-only answer path from permitted release-network operations: `docs/runtime-baseline.md`.
- Integration evidence records command, exit status, environment, model identity, and timing: `docs/progress/issue-26-runtime-probe.json` after the live gate runs.

## Architecture Documentation Boundary

Issue #26 updates only the runtime-baseline portions of [docs/architecture.md](../architecture.md): the first Ollama provider baseline, approved initial `gemma4:12b` generation model, generation/embedding separation, provisional `embeddinggemma` status, loopback answer-path boundary, release-network separation, local process/distribution baseline, first verified environment, and live structured-output probe.

The broader interaction model, retrieval approach, source governance, answer pipeline, and trust-indicator text in [docs/architecture.md](../architecture.md) are project-level context or pre-existing direction. They are not issue #26 completion claims and still require later benchmark evidence, implementation, or human architecture approval before they can be treated as settled production design.

## Commands

```bash
python3 -m unittest discover -s tests -v
python3 -m danish_rag.runtime_probe --policy config/runtime-policy.json --evidence docs/progress/issue-26-runtime-probe.json
```

## Live Runtime Result

Latest evidence file: [docs/progress/issue-26-runtime-probe.json](issue-26-runtime-probe.json)

- Command: `/usr/bin/python3 -m danish_rag.runtime_probe --policy config/runtime-policy.json --evidence docs/progress/issue-26-runtime-probe.json`
- Exit status: `0`
- Provider: Ollama `0.30.6` at `http://127.0.0.1:11434`
- Generation model: `gemma4:12b`
- Reported capabilities: `completion`, `vision`, `audio`, `tools`, `thinking`
- Structured response: `{"runtime_baseline": "mvp-runtime-baseline-issue-26", "status": "ok"}`
- Environment: WSL2 Linux `6.6.114.1-microsoft-standard-WSL2`, Python `3.12.3`, `x86_64`, 16 CPUs, 15908 MB RAM
- Finished at UTC: `2026-07-02T21:28:21.625849+00:00`
- Timings: service version 17.277 ms, model inspection 2.144 ms, structured completion 25805.935 ms, total 25828.447 ms

## Remaining Risks

- The live gate verifies only the current WSL2 host. macOS, native Linux, packaging, and background service behavior remain unverified.
- CPU-only latency is recorded as host evidence, not guaranteed as a support claim.
- Production retrieval libraries, chunking, ranking/reranking, and supported embedding models remain deferred to the retrieval benchmark and later architecture approval.
- Source-review workflow, release signing, and maintainer roles remain deferred outside the runtime baseline.
- Production answer schema, prompt design, citation validation, answer storage, Evidence Confidence calculation, and Fresh Tomato Score calculation remain deferred outside the runtime baseline.
