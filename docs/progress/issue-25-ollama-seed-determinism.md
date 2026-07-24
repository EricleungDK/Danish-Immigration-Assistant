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

## Captured Replay Follow-up, 2026-07-24

The required replay boundary is now implemented as
`--mode captured-live-ollama` with separate `--execution-capture` and
`--capture-report` inputs. It reconstructs the exact answer-path executions,
validates the approved dataset, quality bar, runtime identity, corpus, execution
hashes, review-payload hashes, and companion-report agreement, and records zero
provider calls for the offline scoring run.

The standalone local review page now keeps diagnostic product-owner export
separate from the accepted `final-answer-adjudications-v1` export. Accepted
export requires complete assertion and claim-support decisions plus an explicit
independent-human attestation.

This resolves the replay-architecture prerequisite only.

## Canonical Packet Replacement, 2026-07-24

The original canonical packet E and its companion report were lost from
`/tmp` and cannot be recovered. They were not recreated, renamed, synthesized,
or replaced with `docs/progress/final-answer-evaluation-live.json`.

A new execution was run with `--mode live-ollama` and written directly to the
private evaluation directory as canonical packet G:

- capture:
  `$HOME/.local/share/danish-immigration-rag/private-evaluation/final-answer-capture-g.json`
  (`sha256: 12e567732c0e5c0c12943f54db734c43bcefe1fa1f7185ccd21fb7caf2cfee29`);
- companion report:
  `$HOME/.local/share/danish-immigration-rag/private-evaluation/final-answer-report-g.json`
  (`sha256: 37c46440ef7fd5f3ed2dff9f0c19226fe0d7f87401b8e583a3443e27b91fd0d8`).

Both files have mode `0600`. The companion report records provider `ollama`
version `0.30.6`, generation model `gemma4:12b`, model family and architecture
`gemma4`, quantization `Q4_K_M`, corpus `kr-2026-07-06.1`, ten live answer-path
case executions through the local-only answer path, and zero execution errors.
Packet G contains no human decisions.

The exact packet-G execution SHA-256 bindings are:

- `eval-001-permanent-language-supported`:
  `e352b7c267fdd1832d8bf39a4bab919d7807d0956331bb82ac10026daee7717b`
- `eval-002-exam-type-comparison`:
  `d7af214049d4781f1b126b389c2dd538146ede48a3676c43f2c4f3180c51fac9`
- `eval-003-registration-logistics`:
  `d338ef05488e39843b3c9494c925148dde0c66be9c8ed77d47b039d6f8d6bfa9`
- `eval-004-certificate-equivalence-boundary`:
  `3a3dd24e2711337c6f79f836aa549221b90bc7b93a7c20d533f53f1306089b4f`
- `eval-005-low-risk-ambiguous-exam`:
  `97ae97b703b81f1c9c0a639e26655817571ecc72964f625ad5d68568d6e64e28`
- `eval-006-consequential-ambiguity`:
  `8b8094120acd4908564d9aa9077b52290a17a23a0e57d9c6dbd4a0b9931a2fc6`
- `eval-007-personal-eligibility-request`:
  `9a02148bcfd1760c353c167e5b8e46a6c30e2a0e3b77b0f120f2ac8f1a4489c3`
- `eval-008-legal-advice-request`:
  `02cc037b7e58add7de3a07eb0eac6583cf26e856e6ab3795bbba477ab83cd488`
- `eval-009-citizenship-out-of-scope`:
  `e366e2f65dc7931cb14d99da0d012de4a5ec79761f79cac2934a51dda06536ba`
- `eval-014-citation-validation`:
  `60cbccc7534ad8ffd60988ed7426ab272e768ce920f4e66254295aab6befee45`

An actual `--mode captured-live-ollama` replay consumed packet G and its
companion report, made zero live provider calls, and preserved every execution
hash. Replay now requires the expected whole-file SHA-256 for both inputs before
parsing either file. Focused tests confirm that the replay never constructs the
live runner and fails closed on whole-file, execution, evidence, hash, dataset,
runtime, model, quantization, corpus, case-set, companion-report, or quality-bar
mismatch.

The semantic gate remains blocked until an independent human reviews canonical
packet G and the production evaluator accepts the resulting exact-bound bundle.
No independent-human decision was supplied or inferred here. The other release
blockers remain unchanged.
