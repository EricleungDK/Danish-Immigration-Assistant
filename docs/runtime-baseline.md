# Runtime Baseline

This document records the issue #26 MVP runtime baseline for Danish Immigration RAG. The machine-readable source of truth is [config/runtime-policy.json](../config/runtime-policy.json); the embedded contract below is checked by tests so documentation drift is visible.

## Policy Contract

<!-- runtime-policy-contract:start -->
```json
{
  "baseline_id": "mvp-runtime-baseline-issue-26",
  "initial_provider": "ollama",
  "minimum_ollama_version": "0.30.6",
  "minimum_chromium_major": 150,
  "browser_baseline_reviewed_on": "2026-07-14",
  "initial_generation_model": "gemma4:12b",
  "initial_supported_embedding_model": "embeddinggemma",
  "default_application_bind_host": "127.0.0.1",
  "default_provider_endpoint": "http://127.0.0.1:11434",
  "application_process_model": "single-local-python-process",
  "application_code_updates": "manual",
  "knowledge_release_updates": "explicit-user-approved",
  "answer_path_allows_outbound_requests": false,
  "knowledge_release_checks_allowed": true,
  "answer_path_observed_workflows": [
    "question",
    "retrieval",
    "generation",
    "evidence_inspection",
    "history",
    "deletion",
    "export",
    "local_indexing",
    "knowledge_update_review"
  ],
  "permitted_release_network_operations": [
    "knowledge_release_discovery",
    "approved_knowledge_release_artifact_retrieval",
    "project_release_discovery"
  ],
  "permitted_update_request_fields": [
    "operation",
    "application_version",
    "active_knowledge_release_id",
    "requested_knowledge_release_id",
    "artifact_name"
  ],
  "prohibited_update_request_fields": [
    "question",
    "normalized_question",
    "answer",
    "evidence",
    "conversation_id",
    "conversation_record",
    "turn_index",
    "citation_id",
    "prompt",
    "messages",
    "stable_conversation_derived_identifier"
  ],
  "send_questions_answers_evidence_or_conversation_records_to_updates": false,
  "account_required_for_mvp": false,
  "cloud_history_required_for_mvp": false,
  "remote_inference_credentials_required_for_mvp": false
}
```
<!-- runtime-policy-contract:end -->

## Provider And Models

Ollama is the first MVP provider baseline. This is an initial adapter decision, not a permanent product mandate. Later providers must stay behind provider-specific adapters rather than pretending all local runtimes behave the same.

The approved initial generation model is `gemma4:12b`. It composes evidence-bounded answers from retrieved approved official sources. It is not an approved official source and cannot supply official facts from model knowledge. The live probe validates `/api/show` identity evidence for the approved artifact: `details.family` is `gemma4`, `model_info.general.architecture` is `gemma4`, and `details.quantization_level` is `Q4_K_M`.

Generation and embedding are separate capabilities:

- Generation accepts messages, a response schema, and runtime options, then returns structured output with provider and model identity.
- Embedding accepts text inputs, then returns vectors with model identity and vector dimensions.

`embeddinggemma` is the initial supported embedding model. Issue #4 approved it after the issue #29 retrieval benchmark. Its provider contract, model identity, vector dimensions, corpus identity, and index schema remain separate from generation and must match before an index can be queried.

## Local-only answer path

Questions, retrieved evidence, model inference, generated answers, indexes, and conversation records stay on the user's computer during the answer path. Answer-time browsing, remote inference, remote embedding, and update telemetry containing question or conversation content are outside this baseline.

The application and provider endpoints use loopback defaults:

- Application bind host: `127.0.0.1`
- Ollama endpoint: `http://127.0.0.1:11434`

Non-loopback application exposure is unsupported in the MVP baseline. State-changing browser requests must validate Host and Origin once the web application exists. Provider configuration and any future secrets must not be placed in URLs, logs, or test output. The MVP does not require an account, cloud history, remote inference credential, or provider credential.

## Permitted release-network operations

Release network activity is separate from the local-only answer path. The MVP may check for project and knowledge releases, and it may retrieve an approved knowledge release artifact only after explicit user approval. Knowledge release installation is separate from application-code updates.

Automated privacy observation covers question handling, retrieval, generation, evidence inspection, history, deletion, export, local indexing, and implemented knowledge update controls. Permitted update requests are limited to operation, application version, active knowledge release id, requested knowledge release id, and artifact name.

Permitted update requests must not contain questions, answers, retrieved evidence, conversation records, citation ids, turn indexes, normalized questions, prompts, messages, or stable conversation-derived identifiers.

The running product must not use `git pull` as an update mechanism. Application-code updates are manual for this baseline.

## Process And Distribution

The MVP application shape remains one local Python process serving both the web interface and application endpoints. The issue #26 baseline uses a documented Python virtual environment and foreground launch. Desktop packaging, background services, and automatic application-code installation are not introduced by this issue.

## First Verified Environment

The first verified environment is Windows 11 with WSL2 Ubuntu on x86-64, Python 3.11 or newer, Ollama 0.30.6 or newer, `gemma4:12b`, and an evergreen local browser. For this release-qualification cycle, evergreen means Chrome or Chromium major 150 or newer. The floor was reviewed on 2026-07-14 against the official Chrome desktop stable release and must be reviewed before each release-qualification run rather than remaining a permanent hard-coded assumption.

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
- Installed `gemma4:12b` model identity, Q4_K_M quantization, and completion capability.
- A small structured JSON response using the local provider.
- Environment and timing evidence written to `docs/progress/issue-26-runtime-probe.json`.

Failure diagnostics name the corrective action for a missing service, incompatible Ollama version, missing model, or invalid structured output.
