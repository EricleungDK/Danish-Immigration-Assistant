# Issue #51 engineering remediation

The final candidate execution completed all 20 evaluation surfaces with zero
execution errors. Independent human review was accepted on 2026-09-06. Exact
candidate replay passes every evaluation gate, including all five previously unevaluated
semantic/privacy metrics. This is candidate evaluation, not production release
approval. See the [reviewed replay aggregate](issue-51-reviewed-candidate-replay.json).

## Changes

- Retrieval now preserves question intent, examination identity, source-title
  relevance, and necessary same-document context within the existing top-three
  limit. Signed corpus and index bytes remain unchanged.
- A separate local verification call checks English answers against exact
  official evidence. Bounded witness selection, typed verdicts, strict citation
  aliases, and explicitly selected same-document packages preserve provenance.
  Missing exam/number anchors receive deterministic repair guidance, without
  treating anchor coverage as proof of entailment.
- After one exhausted answer repair, only unchanged, self-contained facts with
  positive semantic verdicts and valid exact witnesses can form an explicitly
  partial answer. Negative duplicate statements cannot reappear through the
  summary. Safety and source warnings remain mandatory. Missing, malformed,
  transport, and resource evidence cannot produce partial success.
- General exam-term questions use the same visible interpretation in retrieval
  and generation. Explicit other purposes do not gain residence context, and
  consequential ambiguity still requires clarification.
- Candidate replay verifies the signed release and exact captured evidence
  without changing global release policy. The review page handles jointly cited
  source context and does not reuse judgments from older context versions.

The semantic verifier uses the configured local generation model in a separate
request. Its decisions are not independent human judgments or a guarantee of
semantic correctness. The approved dataset, thresholds, source bytes, and
canonical packet G remain unchanged.

## Final verification

| Check | Result |
| --- | --- |
| Python suite | 466 tests: 461 passed, five opt-in skips |
| Browser regression suite | 34 passed, one opt-in live skip |
| Actual-candidate live browser journey | Passed; recorded separately |
| Retrieval source coverage | 6/6 residence, 5/5 registration requirements |
| Live passage checks | Three passed |
| Strict live monitors | Privacy, rollback, and supported environment passed |
| Automated workflows | All six validated with bound execution evidence |
| Final capture | 20/20 surfaces completed; zero execution errors |
| Standards and Spec review | No remaining blocking findings |

Evidence: [retrieval](issue-51-retrieval-remediation.json),
[passages](issue-51-live-passage-checks.json),
[candidate browser](issue-51-candidate-browser-check.json),
[strict monitors](issue-51-remediation-monitors.json),
[workflow bundle](issue-51-remediation-workflows/automated-adjudications.json),
[final capture aggregate](issue-51-final-answer-remediation.json),
[exact candidate replay](issue-51-candidate-replay.json), and
[actual review-page readiness](issue-51-review-readiness.json).

The final certificate-equivalence answer is explicitly partial: five verified
facts retained and three unsupported statements omitted. It passed the machine
behavior gate, and the subsequent human review is recorded below. Registration
logistics and the general exam-term answer are complete. Code hashes and the
local generation-model digest remained unchanged during final capture.

## Completed independent review

All ten human answer-case reviews and the independent-review attestation were
accepted alongside the six existing automated workflow records. Strict replay
validated the exact packet, original companion report, and candidate bindings,
with no threshold failures or new provider calls. Original answers, judgments,
dataset, and thresholds were preserved.

Required-fact coverage is 59/60 (98.3%), above the approved 95% threshold. The
single failed required-fact judgment remains recorded. Citation correctness is
47/47, unsupported claims are zero, and forbidden-claim and privacy gates pass.
No evaluation metric remains unevaluated.

The accepted export and full replay report remain in the Git-ignored Windows
project folder `review/private-evaluation/`, named
`danish-rag-final-answer-adjudications-v1.json` and
`issue-51-reviewed-replay-report.json`. Their hashes are recorded in the public
[aggregate](issue-51-reviewed-candidate-replay.json), which contains no private
answers or individual judgments. Pre-review evidence above remains historical.

## Exact human-review packet

Open [the review page](../../review/semantic-adjudication-review.html) locally.
Load this file from the private evaluation directory in the owner's Ubuntu
account:

`issue-51-remediated-20260905T202503Z-capture.json`

In the Windows file picker, the directory is:

```text
\\wsl.localhost\Ubuntu\home\ericl\.local\share\danish-immigration-rag\private-evaluation
```

Its exact companion report is `issue-51-remediated-20260905T202503Z-report.json`. Both files are mode `0600`, outside
Git. A byte-identical copy is available in the Windows project
folder `review/private-evaluation/`. The browser loads the selected packet
locally. The directory server was stopped before copying private evidence.

Candidate replay uses the original signed package at
`data/knowledge_releases/kr-2026-09-05.1` with
`config/trust_roots/project-release-key-v2.json`. The installed corpus directory
has a different artifact layout and is not the signed-package replay input.

| Binding | SHA-256 |
| --- | --- |
| Review packet | `8400d06823ef0b35165fef2bddfd41ef55d7ba98f7e3ee2e380ffbd68d7b54f0` |
| Original companion report | `0c2bdb6ce59410077f98f729a9cad137b0fc90549010e203f336cf9f5d68d309` |
| Candidate manifest | `b154ef00bd63a6a8b5eac526a5e5af7653e7754ac55e1e40fbc72b0d1234b0fb` |

The actual packet was loaded successfully with ten cases, zero recorded human
decisions, an unchecked attestation, and accepted export disabled.

1. Read the question, complete answer, limitations, and source evidence.
2. Judge each required-fact, forbidden-claim, and other assertion independently.
   Mark known failures as failed; use not evaluable when evidence is insufficient.
3. For each claim/citation relationship, check that the explicitly cited context
   supports the complete claim and that the focused citation materially
   contributes. Do not supply missing support from uncited material.
4. Export a diagnostic draft to pause. Only the independent human who personally
   completed every judgment should attest and export accepted adjudications.
5. Keep the export private. Replay the same packet and original report using the
   hashes above, the explicit candidate flags in the README, and the human export.
   Preserve the six workflow records too: use the supplied final monitor/browser/
   provider reports with `--generate-automated-evidence` to merge their machine
   adjudications alongside the human export. Do not regenerate the answer packet.

The five previously pending gates were required-fact coverage, forbidden claims,
privacy requirements, citation correctness, and unsupported-claim rate. All now
pass for the reviewed candidate. Any failed or unevaluated gate continues to block qualification. Manual assistive-technology
verification, off-device signing-key backup, and release-owner approval remain
separate release requirements.

## Preserved diagnostic history

The [original failed run](issue-51-live-qualification.md),
[first intermediate answer capture](issue-51-intermediate-answer-remediation.json),
[second intermediate capture](issue-51-intermediate-answer-remediation-2.json), and
[focused partial-answer check](issue-51-partial-answer-check.json) remain historical
evidence. They are not the packet designated above for human review. An account
usage-limit interruption delayed final verification; the approved runs completed
after the goal resumed.
