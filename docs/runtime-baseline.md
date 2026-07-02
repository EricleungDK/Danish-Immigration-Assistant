# Runtime Baseline

This document records the issue #26 MVP runtime baseline for Danish Immigration RAG. The machine-readable source of truth is [config/runtime-policy.json](../config/runtime-policy.json); the embedded contract below is checked by tests so documentation drift is visible.

## Policy Contract

<!-- runtime-policy-contract:start -->
```json
{
  "baseline_id": "mvp-runtime-baseline-issue-26",
  "initial_provider": "ollama",
  "minimum_ollama_version": "0.30.6",
  "initial_generation_model": "gemma4:12b",
  "provisional_embedding_candidate": "embeddinggemma",
  "default_application_bind_host": "127.0.0.1",
  "default_provider_endpoint": "http://127.0.0.1:11434",
  "application_process_model": "single-local-python-process",
  "application_code_updates": "manual",
  "knowledge_release_updates": "explicit-user-approved",
  "answer_path_allows_outbound_requests": false,
  "knowledge_release_checks_allowed": true
}
```
<!-- runtime-policy-contract:end -->

## Provider And Models

Ollama is the first MVP provider baseline. This is an initial adapter decision, not a permanent product mandate. Later providers must stay behind provider-specific adapters rather than pretending all local runtimes behave the same.

The approved initial generation model is `gemma4:12b`. It composes evidence-bounded answers from retrieved approved official sources. It is not an approved official source and cannot supply official facts from model knowledge.

Generation and embedding are separate capabilities:

- Generation accepts messages, a response schema, and runtime options, then returns structured output with provider and model identity.
- Embedding accepts text inputs, then returns vectors with model identity and vector dimensions.

`embeddinggemma` is only a provisional embedding candidate. It is not a supported embedding model until the retrieval benchmark and later human architecture approval accept it.

## Local-only answer path

Questions, retrieved evidence, model inference, generated answers, indexes, and conversation records stay on the user's computer during the answer path. Answer-time browsing, remote inference, remote embedding, and update telemetry containing question or conversation content are outside this baseline.

The application and provider endpoints use loopback defaults:

- Application bind host: `127.0.0.1`
- Ollama endpoint: `http://127.0.0.1:11434`

Non-loopback application exposure is unsupported in the MVP baseline. State-changing browser requests must validate Host and Origin once the web application exists. Provider configuration and any future secrets must not be placed in URLs, logs, or test output. The MVP does not require provider credentials.

## Permitted release-network operations

Release network activity is separate from the local-only answer path. The MVP may check for project and knowledge releases, and it may retrieve an approved knowledge release artifact only after explicit user approval. Knowledge release installation is separate from application-code updates.

The running product must not use `git pull` as an update mechanism. Application-code updates are manual for this baseline.

## Process And Distribution

The MVP application shape remains one local Python process serving both the web interface and application endpoints. The issue #26 baseline uses a documented Python virtual environment and foreground launch. Desktop packaging, background services, and automatic application-code installation are not introduced by this issue.

## First Verified Environment

The first verified environment is Windows 11 with WSL2 Ubuntu on x86-64, Python 3.11 or newer, Ollama 0.30.6 or newer, `gemma4:12b`, and an evergreen local browser.

For the approved `gemma4:12b` Q4_K_M artifact, 16 GB system RAM is the initial minimum. 24 GB is recommended when generation and indexing overlap. GPU acceleration is recommended. CPU-only compatibility and latency are measured rather than guaranteed.

macOS and native Linux remain candidates for the final supported-environment matrix, but issue #26 does not claim them verified.

## Live Runtime Probe

Run the issue #26 live gate with:

```bash
python3 -m danish_rag.runtime_probe --policy config/runtime-policy.json --evidence docs/progress/issue-26-runtime-probe.json
```

The probe verifies:

- Ollama service reachability at the loopback endpoint.
- Ollama version compatibility with the `0.30.6` floor.
- Installed `gemma4:12b` model identity and completion capability.
- A small structured JSON response using the local provider.
- Environment and timing evidence written to `docs/progress/issue-26-runtime-probe.json`.

Failure diagnostics name the corrective action for a missing service, incompatible Ollama version, missing model, or invalid structured output.
