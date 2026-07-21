# MVP Completion Candidate — 2026-07-14

## Outcome

The Danish Immigration RAG implementation is a release candidate, but an
independent review found that the first supported-environment report did not
exercise a restarted real process/browser. The release remains deliberately
blocked; no production readiness or human review is claimed.

The remaining evidence and decisions are:

1. Independent-human adjudication of the 10 live answer-path cases.
2. Curator admission, official snapshot capture, and named human review of all
   five production sources, followed by a rebuilt signed knowledge release.
3. Replacement supported-environment evidence from a real process/browser,
   including an actual restart and observed environment/browser identity.
4. A human manual assistive-technology check in the published environment.
5. Final production release-owner approval after the preceding gates pass.

## Delivered Product Surface

- One loopback-only FastAPI/Jinja2/HTMX application with local setup,
  conversation history, follow-ups, citations, evidence inspection, export,
  deletion, recovery, and responsive WCAG-oriented UI behavior.
- Real local Ollama generation (`gemma4:12b`) and embeddings
  (`embeddinggemma`) with typed runtime contracts and named probe statuses.
- Metadata-filtered SQLite FTS5 + dense retrieval, fused with RRF `k=60`, and
  compatible corpus/index identity enforcement.
- Evidence-bounded structured generation with citation-ID schema constraints,
  claim-to-citation validation, deterministic safety boundaries, scoped
  refusals, independent Evidence Confidence and Fresh Tomato Score.
- Ed25519-signed knowledge releases with a checked-in public trust root,
  atomic activation/rollback, and staged GitHub metadata/download/review/install
  controls. The monitor exercises the production transport boundary with
  in-memory responses; it does not claim a production release was contacted.
- Fail-closed source registry, source-admission packet generator, final-answer
  evaluator, machine workflow evidence, and release evaluator.

## Verification Evidence

- Current Python regression: 242 tests passed; 2 expected opt-in live skips.
- Focused remediation and release contracts: 91 tests passed. Python AST parsing and the
  supported-environment browser-runner JavaScript syntax check also passed.
- Final retrieval/release/runtime contract regression: 56 tests passed. The
  issue #34 runtime-probe slice separately passed all 24 focused tests.
- Browser automation/accessibility previously passed 19 tests with 1 expected
  opt-in live-Ollama skip, but code changed afterward. A current elevated
  Playwright run is still required and the manual assistive-technology gate is
  separately unperformed.
- Prior opt-in live dense retrieval: 1 passed.
- Prior opt-in live answer smoke: 1 passed.
- Prior live production browser journey: 1 passed, including a contextual
  follow-up. This is historical evidence and does not qualify the new
  real-process/restart monitor contract.
- Runtime probe: passed with Ollama `0.30.6`, `gemma4:12b`, family/architecture
  `gemma4`, quantization `Q4_K_M`; structured completion `23509.025 ms`.
- Retrieval: lexical Recall@1/3/MRR `1.0/1.0/1.0`; dense
  `0.5/1.0/0.75`; hybrid selected with Recall@3 `1.0`.
- The final lexical command wrote fresh evidence to
  `/tmp/issue-27-retrieval-benchmark-final.json` and again reported
  Recall@1/3/MRR `1.0/1.0/1.0`.
- The `2026-07-14T18:04:32Z` monitor passes the privacy boundary and six rollback
  phases. Its eight environment journey checks are diagnostic only because the
  app ran in process without a restart and environment identity was not observed.
- `ollama list` confirms that `gemma4:12b` and `embeddinggemma:latest` are
  installed. Current sandboxed live attempts nevertheless fail because this
  session cannot connect to `127.0.0.1:11434` or bind `127.0.0.1:8917`:
  runtime probe exit `2` (`[Errno 1] Operation not permitted`), live dense exit
  `1`, live answer exit `1` with the safe `503` path, Playwright exit `1`, and
  strict release monitor exit `2`. The elevated browser/monitor attempt was
  rejected before execution by the platform usage limit. No replacement live
  pass is claimed.
- Live final-answer evaluation (`2026-07-14T18:06:02Z`): 20/20 surfaces
  completed, zero execution errors, behavior 14/14, citation coverage 35/35,
  required source-domain coverage 11/11, trust 20/20, Fresh Tomato 13/13,
  zero personal eligibility conclusions, and all six automated workflows
  passed.
- Current derived release evaluation preserves the
  immutable legacy monitor SHA-256 `6f9d4f10...`, passes the privacy and rollback
  components, rejects the environment component under the current evidence
  contract, marks browser accessibility `not_verified`, and exits `1` in strict
  mode as designed.

The five semantic final-answer metrics remain `not_evaluable`, not passed:
required-fact coverage, forbidden claims, privacy-requirement compliance,
citation correctness for relationships requiring review, and unsupported-claim
rate. Strict final-answer evaluation therefore exits `1` as designed.

## Traceability

- Runtime evidence: `docs/progress/issue-26-runtime-probe.json`
- Retrieval evidence: `docs/progress/issue-27-retrieval-benchmark.json`,
  `docs/progress/issue-28-dense-retrieval-benchmark.json`, and
  `docs/progress/issue-29-hybrid-retrieval-comparison.json`
- Release monitor: `docs/progress/release-monitors-live.json`
- Final-answer report: `docs/progress/final-answer-evaluation-live.json`
- Workflow bundle: `docs/progress/final-answer-machine-evidence/`
- Release decision: `config/release-qualification.json`
- Derived release report: `docs/progress/release-evaluation-current.json`
- Source status: `data/source_registry/sr-2026-07-06.1.json`

## Private Human Packets

These packets are mode `0600`, remain outside the repository, and contain
blank human fields:

- `/tmp/final-answer-human-review-live.json`
  - SHA-256: `5bbeda0e1831ff9586912b302c9be505fbc4f4469dd73e87c65731eb0f40446f`
- `/tmp/danish-rag-source-admission-packet.json`
  - SHA-256: `0ef789d795c0eba51395933a1d1b2a22829a8910a49787c531019fc0b48cd9d6`

Do not commit either packet. Do not fill decisions, reviewer identities, test
results, or source approvals on behalf of a human.

## Repository And Issue Handoff

- GitHub currently reports issues #1 and #34 as open. Issue #34's declared
  blockers #31, #32, and #33 are closed, and its implementation acceptance
  criteria are covered by the 24-test focused pass plus the 242-test full pass.
- The GitHub CLI token for `EricleungDK` is invalid, so this session could not
  post the concise verification evidence or close #34. Re-authenticate with
  `gh auth login -h github.com` before performing either write.
- The repository is on `main` at `0b3562a` with a large uncommitted worktree.
  The session exposes `.git` read-only and the repository has no
  `scripts/git_*.sh` workflow helpers, so no feature branch, commit, push, PR,
  issue comment, or issue closure is claimed.

## Continuation Revalidation — 2026-07-14T19:31:03Z

The external conditions were checked again before declaring an impasse:

- `ollama list` still succeeds and shows `gemma4:12b` plus
  `embeddinggemma:latest`.
- `.venv/bin/python -B -m danish_rag.runtime_probe --evidence
  /tmp/issue-26-runtime-probe-continuation.json --timeout 120` exits `2` with
  `<urlopen error [Errno 1] Operation not permitted>`.
- `npm run test:browser` exits `1` because the application cannot bind
  `127.0.0.1:8917`.
- `.venv/bin/python -B -m danish_rag.release_evaluation --strict --output
  /tmp/release-evaluation-continuation.json` exits `1`. The generated report
  remains `blocked` / `do-not-release` with `strict_release_passed: false`.
- `gh auth status` still reports that the active `EricleungDK` token is
  invalid.

The strict report continues to identify the same five blockers: independent
answer adjudication, replacement real-process/browser supported-environment
evidence, production source qualification, current browser plus manual
assistive-technology evidence, and final release-owner approval.

## Release Decision

`blocked` / `do-not-release`.

The current corpus is a signed, mechanically valid project-authored fixture,
not a production-qualified official-source release. Final publication must
remain blocked until all remaining gates above are recorded and the strict
evaluators are rerun against those exact evidence hashes.
