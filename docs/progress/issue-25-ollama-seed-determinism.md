# Issue 25 Ollama Seed Determinism

GitHub issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/25

Evaluation quality bar: [docs/evaluation-quality-bar.md](../evaluation-quality-bar.md)

Release qualification: [docs/release-qualification.md](../release-qualification.md)

## Finding

Production Ollama structured-chat requests and the structured runtime probe now
send the same deterministic runtime options:

```json
{
  "temperature": 0,
  "seed": 0
}
```

This follows Ollama's
[documented reproducible chat-request contract](https://github.com/ollama/ollama/blob/main/docs/api.md#chat-request-reproducible-outputs).
The OpenAI-compatible provider path is unchanged because its provider contract
does not establish the same request option.

## TDD Record

- Red: the focused answer-path, runtime-probe, evidence-safety, and final-answer
  evaluation suite had three failures because ordinary Ollama generation,
  bounded social generation, and the structured runtime probe sent
  `{"temperature": 0}` without a seed.
- Green: the same 42-test suite passed after the shared seed was added.
- Adjacent runtime-policy, privacy, evaluation-contract, machine-evidence,
  release-evaluation, and live-smoke modules passed 53 tests with one documented
  opt-in live-smoke skip.

## Live Verification

Two fresh private final-answer executions were run on 2026-07-23 with the
approved saved provider configuration. Both reported:

- provider `ollama` version `0.30.6`;
- generation model `gemma4:12b`;
- family and architecture `gemma4`;
- quantization `Q4_K_M`;
- ten live answer-path executions; and
- zero execution errors.

Exact packet comparison still reported generated-result drift in seven cases:

- `eval-001-permanent-language-supported`
- `eval-002-exam-type-comparison`
- `eval-003-registration-logistics`
- `eval-004-certificate-equivalence-boundary`
- `eval-007-personal-eligibility-request`
- `eval-009-citizenship-out-of-scope`
- `eval-014-citation-validation`

The content-free structural diagnostic found no change in retrieved evidence
IDs, order, content, metadata, or scores. Drift remained confined to generated
answer fields. The private packets, generated answers, retrieved evidence, and
execution hashes remain outside the repository.

## Release Consequence

A fixed seed is necessary but did not make fresh `gemma4:12b` executions exactly
reproducible in the approved local runtime. Approximate comparison must not
replace exact evidence binding, and adjudications for changed execution hashes
must not be reused.

Before final-answer adjudication can become durable release evidence, the
evaluation architecture needs an exact captured-execution replay path. That path
must:

- consume a private captured execution without calling the generation model;
- validate its dataset, quality-bar, provider/model, corpus, and execution
  bindings before scoring;
- preserve the exact execution SHA-256 used by the human-review packet; and
- reject missing, changed, or mismatched captures rather than rerunning the
  model or accepting semantic similarity.

Release remains blocked until that replay boundary exists and an independent
human supplies an attestation and adjudication bundle bound to the final exact
packet.
