# Live Release Evaluation Runbook

Operator runbook for collecting and replaying live release evidence. Moved from the root `README.md`; commands and hashes are unchanged. Gate state of record: [release-qualification.md](release-qualification.md).

The live evaluator uses the same default per-user provider configuration and
data directories as the application: `$XDG_CONFIG_HOME` and `$XDG_DATA_HOME`
when set, otherwise `~/.config/danish-immigration-rag/provider-config.json` and
`~/.local/share/danish-immigration-rag`. Do not set temporary XDG overrides
when collecting release evidence.

First run the strict live release monitors:

```bash
.venv/bin/python -B -m danish_rag.release_monitors \
  --mode live \
  --output docs/progress/release-monitors-live.json \
  --generated-at-utc "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --strict
```

The original [issue #51 qualification run](progress/issue-51-live-qualification.md)
failed. The [engineering remediation](progress/issue-51-engineering-remediation.md)
records the subsequent fixes, and the [acceptance handoff](progress/issue-51-completion.md)
records the current strict replay. The [reviewed candidate replay](progress/issue-51-reviewed-candidate-replay.json)
passes every evaluation threshold. Production qualification and final owner
approval are recorded separately in [release-qualification.md](release-qualification.md)
and [progress/release-owner-approval-20260908.json](progress/release-owner-approval-20260908.json).
Packet G is unchanged, and publication has not been performed.

Canonical private packet G was generated directly with the approved local
runtime, model, and corpus. Both outputs are private mode-`0600` evidence. Do
not rerun this command unless deliberately replacing the canonical execution:

```bash
umask 077
.venv/bin/python -B -m danish_rag.final_answer_evaluation \
  --mode live-ollama \
  --output "$HOME/.local/share/danish-immigration-rag/private-evaluation/final-answer-report-g.json" \
  --human-review-packet "$HOME/.local/share/danish-immigration-rag/private-evaluation/final-answer-capture-g.json" \
  --generated-at-utc 2026-07-24T17:16:25Z
```

Packet E and its companion report were lost from `/tmp` and cannot be
recovered; do not recreate, rename, or synthesize packet E. Do not substitute
`docs/progress/final-answer-evaluation-live.json` for packet G's companion
report.

Replay canonical packet G without calling Ollama by pinning both exact private
file hashes:

```bash
.venv/bin/python -B -m danish_rag.final_answer_evaluation \
  --mode captured-live-ollama \
  --execution-capture "$HOME/.local/share/danish-immigration-rag/private-evaluation/final-answer-capture-g.json" \
  --execution-capture-sha256 12e567732c0e5c0c12943f54db734c43bcefe1fa1f7185ccd21fb7caf2cfee29 \
  --capture-report "$HOME/.local/share/danish-immigration-rag/private-evaluation/final-answer-report-g.json" \
  --capture-report-sha256 37c46440ef7fd5f3ed2dff9f0c19226fe0d7f87401b8e583a3443e27b91fd0d8 \
  --output /tmp/danish-rag-captured-replay-g.json \
  --generated-at-utc "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

Captured replay rejects any whole-file hash or validated internal binding
mismatch. The command above intentionally performs an unadjudicated diagnostic
replay, records `live_provider_calls: false`, and remains non-strict; it is not
the current qualification result. The current candidate's accepted strict
replay is recorded in
[`issue-51-reviewed-candidate-replay.json`](progress/issue-51-reviewed-candidate-replay.json)
and uses the private adjudication export described in the evaluation-quality-bar
procedure.

For a **new candidate** whose corpus differs from the release-policy corpus,
keep the same exact packet/report hash arguments and additionally supply
`--candidate-release-dir /path/to/signed/release`,
`--candidate-manifest-sha256 <exact-manifest-sha256>`, and
`--trust-root-path /path/to/trusted/root.json`. Replay verifies the signature,
artifact integrity, and every captured evidence field against that candidate.
It still requires error-free executions and the approved runtime/model identity.
The resulting report is scoped `explicit-candidate-only`; it does not promote
release policy or replace canonical packet G.

Open [the local semantic review page](../review/semantic-adjudication-review.html)
and load the exact packet named in the relevant review handoff. Review each
answer and its cited evidence. Mark unsupported claims as failed and use
not evaluable when the evidence is insufficient to decide. Only the independent
human reviewer should complete the attestation and export the adjudication
bundle. Supply that private export with `--adjudications /path/to/export.json`
when replaying the same exact packet; workflow evidence is collected separately.

The current candidate's independent review was accepted on 2026-09-06 and its
strict replay passed all 20 surfaces with zero unevaluated metrics. The public
aggregate retains counts and hashes but no private answers or adjudications;
see [the reviewed replay](progress/issue-51-reviewed-candidate-replay.json).

## Historical pre-review evidence (2026-07-14)

The live run generated at `2026-07-14T18:06:02Z` completed all 20 surfaces with
zero execution errors. Behavior, structural, source-domain, citation-coverage,
trust-indicator, freshness, personal-conclusion, and automated-workflow gates
passed. Five semantic metrics were `not_evaluable` because independent human
adjudication had not yet been recorded. This historical report does not
establish current production qualification; the accepted status is recorded in
[the release qualification](release-qualification.md) and
[the current acceptance handoff](progress/issue-51-completion.md).
See also [the historical machine-readable report](progress/final-answer-evaluation-live.json).
