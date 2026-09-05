# Issue #51 — Production-candidate live qualification evidence

Recorded 2026-09-05 for [issue #51](https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/51).
Evidence collection is complete; qualification remains **blocked / do-not-release**.
No thresholds, approved datasets, release policy, or application answer behavior changed.

## Machine findings

The exact installed candidate is `kr-2026-09-05.1`, with 52 semantic chunks.
The [retrieval report](issue-51-grounded-flexibility-retrieval.json) binds its
installed manifest, dense-index bytes, schema, and inspected `embeddinggemma`
identity. The retriever verifies the signed installed release and compatible
index before running the unchanged nine-case grounded-flexibility dataset.
Chunk results receive credit through their source-document identity, and only
eligible evidence receives credit.

| Check | Observation | Qualification |
| --- | --- | --- |
| Permanent-residence required-source coverage at 3 | 5/6 (83.3%) | Failed; requires 100% |
| Registration required-source coverage at 3 | 4/5 (80%) | Failed; requires 100% |
| Retrieval execution errors / blocked-source results | 0 / 0 | Passed |
| Live answer-path executions | All 10 attempted; 8 `AnswerValidationError`, 2 completed | Failed |
| Controlled source-policy scenarios | 4 completed | Controlled evidence only |
| Separate workflow surfaces | 6 unevaluated | Blocking |
| Independent-human semantic adjudication | Not supplied | Blocking |

The [public aggregate handoff](issue-51-live-qualification.json) records the
complete final-answer metric statuses and threshold failures. The live runner
used the installed production retrieval path and configured local Ollama
`0.30.6` / `gemma4:12b` (`Q4_K_M`). Separate local model observations during and
after execution agree on digest
`4eb23ef187e2c5462566d6a1d3bbbc2f1346d0b4327cbb66d58fffbcc9b2b05c`.
The four source-policy scenarios use the existing controlled harness; they are
not live production-corpus answer evidence. Grounded-flexibility answer semantics
were not evaluated by this retrieval-only run.

Failed or unevaluated citation, privacy, trust, behavior, semantic, and workflow
metrics remain blocking, even where a partial numeric observation looks passing.
The nine-case grounded-flexibility dataset defines required-source coverage but
no separate forbidden-result list; no forbidden-result pass is inferred.
The earlier five-case issue #50 installation check does not replace this suite.

## Private human-review handoff

Both files are outside the repository, mode `0600`, at:

```text
/home/ericl/.local/share/danish-immigration-rag/private-evaluation/issue-51-final-answer-capture.json
/home/ericl/.local/share/danish-immigration-rag/private-evaluation/issue-51-final-answer-report.json
```

Their exact SHA-256 values are in the public handoff's `private_evidence` object.
All ten packet executions match their companion-report execution hashes; each
review-payload hash was recomputed and verified. The dataset hash and capture
timestamp also agree. The packet retains exact synthetic prompts, retrieved
evidence, completed answers, failure types, and blank independent-human templates.
Failed executions contain a null result; rejected model output is not retained by
the existing evaluator. This is a diagnostic review packet, not a complete set
of successfully generated answers ready for final semantic qualification.

Public files contain hashes, model/corpus provenance, and aggregate observations;
they omit prompts, answer text, conversation identifiers, conversation records,
and adjudication content. No private packet was committed. Canonical packet G
and its companion report were not modified or reused.

An independent human must separately assess exact successful executions for
required facts, forbidden claims, privacy prose, citation correctness, and
unsupported claims. Reviewers must not turn failed executions into passes or
invent answers. First resolve the retrieval and answer-validation failures, then
capture a new packet under new filenames and obtain independent adjudications
bound to those exact bytes. Keep this failed run as evidence. Workflow evidence
must also be collected separately; historical fixture passes do not qualify it.

The current `captured-live-ollama` replay validator requires an error-free report
and still pins the older release-policy corpus. It deliberately rejects this
failed candidate packet; this work does not relax that gate or promote the
candidate by changing release policy.

## Reproduction and verification

Run the content-free retrieval collector against the installed candidate:

```bash
.venv/bin/python -B -m danish_rag.grounded_flexibility_retrieval \
  --trust-root-path config/trust_roots/project-release-key-v2.json \
  --output /tmp/grounded-flexibility-retrieval-new-run.json
```

It exits `1` on failed retrieval thresholds. It uses no generation or conversation
store. To deliberately create a **new** live answer packet, choose unused private
filenames; do not overwrite this run or packet G:

```bash
umask 077
.venv/bin/python -B -m danish_rag.final_answer_evaluation \
  --mode live-ollama \
  --trust-root-path config/trust_roots/project-release-key-v2.json \
  --output "$HOME/.local/share/danish-immigration-rag/private-evaluation/issue-51-next-report.json" \
  --human-review-packet "$HOME/.local/share/danish-immigration-rag/private-evaluation/issue-51-next-capture.json" \
  --strict
```

The captured run exited `1`, as required by its failures. Local Ollama and test
servers required execution outside the socket-restricted sandbox. No remote
inference was used.

Targeted collector tests cover source credit for chunks, rejection of blocked
sources, and exception/prompt/output privacy (3 passed). Full Python validation:
359 tests, 357 passed and 2 opt-in skips. Playwright: 30 passed and 1 opt-in skip.
Python parse and whitespace checks passed. The repository has no configured
static typechecker. These software tests do not erase the failed live evidence.
Separate code-review agents reported zero Standards findings and zero Spec
findings. Final checks confirmed the public/private file hashes, packet internal
bindings, and installed candidate identity agree, with no prohibited private
content fields in the public evidence.
