# PRD: Local Runtime and Retrieval Benchmark Baseline

**Status:** Approved for implementation
**Date:** 2026-07-02
**Parent product PRD:** GitHub issue #1
**Delivery scope:** GitHub issues #2 and #3
**Canonical vocabulary:** `CONTEXT.md`
**Architecture authority:** `docs/architecture.md`

## Problem Statement

Danish Immigration RAG cannot safely begin production answer-pipeline work until two foundations are settled and evidenced.

First, the product needs a concrete local runtime baseline. Users must be able to run a generation model without sending questions, retrieved evidence, answers, or conversation records to a remote service. The architecture requires manual provider configuration, separate generation and embedding capabilities, and provider-specific adapters, but it does not yet name the initial provider, generation model, browser-security baseline, process lifecycle, application distribution path, supported environment, or minimum hardware. Without those decisions, later implementation tickets would make inconsistent assumptions and could accidentally turn an initial provider into a permanent product dependency.

Second, the product needs measured retrieval evidence before choosing production retrieval libraries, indexing strategy, chunking, ranking, or a supported embedding model. A plausible answer from a generation model must never hide failed retrieval. The benchmark must therefore test lexical, dense, metadata-filtered, and hybrid retrieval against a small reviewed corpus and explicit expected results. It must expose blocked-source violations, missed required evidence, latency, indexing cost, storage, memory, model identity, and re-index compatibility.

The user has approved Ollama with the locally installed `gemma4:12b` generation model for the first MVP runtime. This approval does not make Ollama mandatory forever and does not make `gemma4:12b` a factual source. The generation model composes evidence-bounded answers; approved official sources remain the only evidence for official facts.

## Solution

Deliver the work as two sequential, independently verifiable outcomes.

Issue #2 establishes an Ollama-first but provider-neutral runtime baseline. It records machine-readable and human-readable decisions for local provider capabilities, generation and embedding separation, loopback-only browser exposure, local-process lifecycle, secret handling, manual application updates, supported environments, and minimum hardware. It validates the approved `gemma4:12b` model with a live Ollama integration test that inspects the model and requests a small structured response.

Issue #3 then builds a disposable, reproducible retrieval benchmark. The benchmark uses project-authored fixtures representing approved official sources and explicit evaluation queries. It compares SQLite FTS5 lexical retrieval, dense retrieval using an evaluated local embedding candidate, and hybrid reciprocal-rank fusion after common metadata eligibility filtering. It reports retrieval quality separately from generation, measures operational cost, records index compatibility metadata, and produces an evidence-backed recommendation without committing unapproved production retrieval code.

The work follows test-driven development. Each behavior is introduced through one failing public-interface test, the minimum implementation needed to pass it, and a refactor only while green. Issue #2 must pass its live runtime integration gate and have its documentation updated before issue #3 begins. Issue #3 must pass its full live embedding benchmark and generate complete results before it is considered finished.

## User Stories

1. As a privacy-conscious user, I want all model inference to run locally, so that my questions and retrieved evidence do not leave my computer.
2. As a first-time user, I want a documented initial provider baseline, so that I know which local runtime is supported.
3. As a user who already runs Ollama, I want the MVP to support my installed runtime, so that I can start without adopting another provider.
4. As a user, I want `gemma4:12b` to be validated before it is named as supported, so that setup does not promise an unusable model.
5. As a user, I want the application to test provider connectivity and model capabilities, so that configuration failures are actionable.
6. As a user, I want generation and embedding treated as different capabilities, so that the most appropriate local model can perform each job.
7. As a user, I want the first Ollama integration to remain replaceable, so that future compatible providers can be added without rewriting the answer pipeline.
8. As a user, I want provider identity and model identity recorded locally, so that generated answers can remain auditable.
9. As a user, I want provider secrets excluded from URLs and logs, so that local configuration does not create avoidable exposure.
10. As a user, I want the local web application bound to loopback by default, so that it is not accidentally exposed to my network.
11. As a user, I want unsupported non-loopback exposure rejected or clearly outside support, so that the privacy boundary is explicit.
12. As a user, I want state-changing browser requests protected by Host and Origin validation, so that another site cannot silently control my local application.
13. As a user, I want one documented local application process, so that startup and shutdown are understandable.
14. As a user, I want application-code updates kept separate from knowledge releases, so that source updates cannot silently replace executable code.
15. As a user, I want application-code updates to require an explicit manual action in the MVP, so that software changes remain under my control.
16. As a user, I want a published supported-environment baseline, so that I can determine whether my computer is expected to work.
17. As a user, I want minimum and recommended memory requirements for `gemma4:12b`, so that I can anticipate whether local generation and indexing are practical.
18. As a user without GPU acceleration, I want CPU-only behavior measured rather than promised, so that support claims remain factual.
19. As a maintainer, I want runtime decisions machine-readable, so that automated tests can detect drift between code and documentation.
20. As a maintainer, I want a live runtime probe separate from unit tests, so that normal development remains deterministic while release evidence uses the real provider.
21. As a maintainer, I want a structured-output integration test, so that later evidence-bounded answer schemas have a validated provider capability.
22. As a maintainer, I want a missing Ollama service to fail clearly, so that absence cannot be mistaken for a passing integration test.
23. As a maintainer, I want a missing or incompatible model to name the corrective action, so that runtime failures are diagnosable.
24. As a product owner, I want retrieval architecture selected from measurements, so that a familiar library is not mistaken for a justified design.
25. As a product owner, I want lexical retrieval measured independently, so that exact Danish terminology receives appropriate weight.
26. As a product owner, I want semantic retrieval measured independently, so that English paraphrases can be evaluated without lexical bias.
27. As a product owner, I want hybrid retrieval compared with both single-mode candidates, so that added complexity must demonstrate value.
28. As a user, I want exact Danish examination terms to retrieve the relevant approved official source, so that official terminology works reliably.
29. As a user, I want equivalent English phrasing to retrieve the relevant approved official source, so that I can ask questions without Danish fluency.
30. As a user, I want terse questions to remain retrievable, so that short natural queries can still find evidence.
31. As a user, I want realistic spelling mistakes tested, so that minor errors do not silently destroy retrieval quality.
32. As a user, I want metadata filters applied before evidence is credited, so that irrelevant or ineligible content cannot support an answer.
33. As a user, I want changed-unreviewed sources blocked from retrieval credit, so that unapproved changes cannot become official facts.
34. As a user, I want broken and extraction-failed sources blocked, so that unusable evidence cannot be cited.
35. As a user, I want unapproved sources excluded, so that only reviewed approved official sources support factual answers.
36. As a user, I want overdue but policy-usable evidence distinguished from healthy evidence, so that stale material is not silently treated as current.
37. As a maintainer, I want every benchmark query to declare required and forbidden evidence, so that success is objective and reviewable.
38. As a maintainer, I want retrieval misses reported even when the generation model knows a plausible answer, so that pretrained knowledge cannot mask missing evidence.
39. As a maintainer, I want Recall@1, Recall@3, and mean reciprocal rank, so that retrieval quality is visible at useful ranking depths.
40. As a maintainer, I want blocked-source and forbidden-result violations reported separately, so that unsafe results cannot be averaged away.
41. As a maintainer, I want query latency measured, so that retrieval quality can be considered alongside user-visible cost.
42. As a maintainer, I want indexing wall time measured, so that corpus-installation cost is explicit.
43. As a maintainer, I want index storage measured, so that local disk requirements can be planned.
44. As a maintainer, I want peak memory measured, so that retrieval candidates can be compared on the supported hardware baseline.
45. As a maintainer, I want cold model loading separated from warm retrieval latency, so that embedding runtime cost is not misrepresented.
46. As a maintainer, I want embedding model identity and vector dimensions stored with index metadata, so that incompatible indexes are rejected.
47. As a user, I want a model change to require re-indexing, so that embeddings from incompatible models are never mixed.
48. As a maintainer, I want benchmark fixtures to contain no user conversation data, so that evaluation does not undermine privacy.
49. As a maintainer, I want benchmark inputs and outputs versioned and reproducible, so that another developer can verify the recommendation.
50. As a maintainer, I want failed benchmark output written atomically, so that an incomplete run cannot masquerade as final evidence.
51. As a product owner, I want the recommendation to identify rejected alternatives and tradeoffs, so that later architecture review has a clear record.
52. As a product owner, I want the benchmark to recommend hybrid retrieval only when it meets explicit rules, so that complexity is earned by measured behavior.
53. As a product owner, I want production release thresholds kept separate from benchmark baselines, so that issue #3 does not silently decide issue #7.
54. As an implementing agent, I want one public benchmark runner, so that the highest-level integration seam verifies fixtures, indexes, rankings, metrics, and reports together.
55. As an implementing agent, I want issue-specific progress documentation, so that tests, commands, results, and remaining risks are traceable.
56. As a reviewer, I want issue #2 completed before issue #3 begins, so that retrieval measurements use an approved runtime baseline.

## Implementation Decisions

### Delivery order and authority

- Issue #2 is completed first. Its contract tests, live integration probe, architecture update, and progress report must pass review before issue #3 implementation starts.
- Issue #3 consumes the issue #2 runtime baseline and produces benchmark evidence plus a recommendation. It does not silently modify the approved runtime decision.
- `CONTEXT.md` remains authoritative for domain language. `docs/architecture.md` remains authoritative for settled architecture. This PRD supplies implementation requirements for issues #2 and #3.

Acceptance criteria:

- Issue-specific documentation links the relevant GitHub ticket, this PRD, tests, integration command, results, and unresolved risks.
- No issue #3 completion claim is made without evidence that issue #2's integration gate passed first.

### Provider and model baseline

- Ollama is the first supported MVP provider.
- `gemma4:12b` is the initial generation model approved by the user.
- The minimum Ollama version is `0.30.6`, matching the locally validated client and exceeding the installed model's `0.30.5` requirement.
- Ollama is an initial adapter, not a permanent product mandate. Provider-specific differences remain behind independent adapters.
- Generation and embedding are separate capability contracts. A generation capability accepts messages, a response schema, and runtime options. An embedding capability accepts text inputs and returns vectors plus model identity and dimensions.
- `embeddinggemma` is the first embedding candidate, not a supported embedding model until issue #3 evidence and the subsequent human architecture gate approve it.
- The generation model is never treated as an approved official source.

Acceptance criteria:

- Machine-readable runtime policy identifies Ollama, the version floor, `gemma4:12b`, separate capability boundaries, and the provisional embedding candidate.
- Tests fail if the documented model/provider baseline and machine-readable policy disagree.
- The runtime integration probe confirms the installed model identity, completion capability, and structured JSON response.
- A missing service, missing model, incompatible version, or invalid structured output fails with an actionable diagnostic.

### Local-only runtime and browser security

- The application and Ollama endpoint use loopback addresses by default.
- Non-loopback application exposure is unsupported in the MVP baseline.
- The later application implementation must validate Host and Origin on state-changing browser requests.
- Provider configuration and user content must not be placed in URLs.
- The MVP does not require provider credentials. Any future credential-bearing adapter requires a separate secret-storage decision.
- Provider identity, model identity, version, and endpoint remain local runtime metadata and are excluded from update telemetry alongside all question and conversation content.

Acceptance criteria:

- Contract tests reject a default application or provider endpoint that is not loopback.
- Runtime documentation explicitly distinguishes local answer-path operations from permitted release-network operations.
- No test or integration output exposes provider secrets or user content.

### Process, distribution, and supported environment

- One local Python process serves the UI and application endpoints.
- The initial distribution path uses a documented Python virtual environment and foreground launch.
- Application-code updates are manual and separate from knowledge releases. The running product never uses `git pull` as an update mechanism.
- The first verified environment is Windows 11 with WSL2 Ubuntu on x86-64, Python 3.11+, Ollama 0.30.6+, and an evergreen local browser.
- For the approved `gemma4:12b` Q4_K_M artifact, 16 GB system RAM is the initial minimum and 24 GB is recommended when generation and indexing overlap.
- GPU acceleration is recommended. CPU-only compatibility and latency are measured rather than guaranteed.
- macOS and native Linux remain candidates for the final supported-environment matrix and are not claimed verified by this work.

Acceptance criteria:

- The runtime policy and documentation publish minimum and recommended requirements without claiming untested platforms.
- The live integration report records the actual environment and observed model timing.
- Desktop packaging, background services, and automatic application-code installation are not introduced.

### Retrieval fixture contract

- Corpus fixtures contain stable identifiers, titles, publishers, official URLs, topic tags, language, approval state, source health, check timestamps, and project-authored content.
- Evaluation queries contain stable identifiers, query text, category, required document identifiers, forbidden document identifiers, allowed source-health states, and optional metadata filters.
- Fixtures cover permanent-residence language requirements, Danish examination types, examination registration logistics, and certificate/equivalence boundaries.
- Query categories include exact Danish terminology, English paraphrase, terse phrasing, realistic typo, metadata filtering, stale-but-policy-usable evidence, and blocked-source exclusion.
- Fixtures are synthetic or short project-authored paraphrases and contain no user questions or copied webpages.

Acceptance criteria:

- Invalid or incomplete fixtures fail before indexing.
- Every required query category has at least one reviewed case.
- Blocked and stale source states are represented distinctly.
- Required and forbidden results are explicit and require no generation-model judgment.

### Retrieval candidates and eligibility

- Lexical retrieval uses SQLite FTS5 with Unicode tokenization and BM25 ordering.
- Dense retrieval uses cosine similarity over vectors returned by the local Ollama embedding endpoint.
- Hybrid retrieval uses deterministic reciprocal-rank fusion of lexical and dense rankings.
- The same metadata eligibility filter runs before any candidate result can receive evaluation credit.
- Changed-unreviewed, broken, extraction-failed, and unapproved content is blocked. Overdue but otherwise approved content remains identifiable for policy-usable stale scenarios.
- Dense index metadata records embedding model identity, vector dimensions, corpus fixture identity, and schema version. Any mismatch requires re-indexing.

Acceptance criteria:

- Exact-term behavior can be evaluated independently through lexical retrieval.
- English-paraphrase behavior can be evaluated independently through dense retrieval.
- Hybrid output is stable for identical inputs.
- No blocked source can count as a successful retrieval.
- Model or dimension mismatch invalidates the dense index before querying.

### Benchmark measurements and recommendation

- Quality metrics include Recall@1, Recall@3, mean reciprocal rank, forbidden-result violations, and blocked-source violations.
- Operational metrics include per-query latency, aggregate latency, indexing wall time, on-disk index size, and process peak resident memory.
- Cold embedding-model load effects and warm retrieval latency are reported separately when observable.
- Results include runtime version, embedding model identity, dimensions, fixture identity/hash, configuration, and execution timestamp.
- Retrieval failures remain visible even if `gemma4:12b` could generate a plausible answer.
- Hybrid retrieval is recommended only when it has no blocked-source violations, does not regress exact-term Recall@3 against lexical retrieval, matches or improves English-paraphrase and typo Recall@3 against both single-mode candidates, records complete compatibility metadata, and operates within the issue #2 hardware baseline.
- If hybrid does not satisfy the rule, the report recommends the best evidenced alternative and records why hybrid was rejected.
- Final production thresholds belong to the later evaluation decision ticket; issue #3 supplies baselines only.

Acceptance criteria:

- One command runs fixture validation, index creation, all retrieval candidates, measurements, and report generation.
- Machine-readable results and a human-readable recommendation agree on candidate metrics and selected recommendation.
- The benchmark exits unsuccessfully when fixtures, embeddings, index compatibility, or report generation fail.
- Output publication is atomic; a failed run cannot replace the last complete result.

### Documentation progress requirements

- Issue #2 progress documentation is updated during implementation and finalized after its integration test.
- Issue #3 progress documentation records every TDD behavior, full integration command, measured results, recommendation, and limitations.
- Architecture documentation is updated only with decisions proven and approved by the relevant issue gate.
- The project README links the runtime baseline, benchmark reproduction command, and recommendation when completed.
- Generated benchmark measurements are clearly distinguished from hand-authored decisions.

Acceptance criteria:

- A reviewer can trace each GitHub acceptance criterion to documentation and executable evidence.
- Documentation never claims a passing integration gate without the recorded command and fresh result.

## Testing Decisions

- Tests verify public behavior, not private implementation. Renaming an internal helper must not break a test if observable behavior remains unchanged.
- TDD uses vertical red-green-refactor cycles: one behavior test is written and observed failing, minimum code makes it pass, and refactoring occurs only while green.
- The preferred seam count is two because the tickets have different external authorities:
  - The issue #2 seam is a live runtime probe through the local Ollama HTTP API, covering service reachability, version, model inspection, and structured generation.
  - The issue #3 seam is the public benchmark runner, covering fixture validation, eligibility, index creation, all retrieval candidates, metrics, compatibility metadata, and report generation.
- Lower-level unit tests are added only where a high-level failure cannot isolate risky behavior, such as fixture validation, blocked-source eligibility, rank fusion, metric calculation, and index compatibility.
- Issue #2 contract tests cover provider neutrality, generation/embedding separation, version floor, model identity, loopback defaults, update separation, and environment requirements.
- The issue #2 integration test is opt-in for normal development but mandatory before ticket completion. An unreachable service is a failure, not a skip, when integration mode is enabled.
- Issue #3 starts with a single eligible exact-term query and grows one behavior at a time: FTS5 retrieval, blocked-source exclusion, explicit evaluation, live embedding metadata, dense ranking, deterministic fusion, measurement serialization, and recommendation generation.
- The issue #3 final integration test uses the live local embedding endpoint and the complete reviewed fixture set.
- Retrieval evaluation is independent from generation. No test uses `gemma4:12b` output as evidence that retrieval succeeded.
- Test fixtures contain no production user data or personal questions.
- The repository currently has no production test prior art. The throwaway UI prototype's browserless checks are design evidence, not a test framework to reuse.
- Integration evidence records exact commands, exit status, environment, model identity, and summarized timings.

## Out of Scope

- Production answer generation, prompt design, citation validation, or Conversation First UI implementation.
- An autonomous agent loop or model-selected tools.
- Answer-time browsing, cloud inference, or remote embedding.
- Supporting multiple generation providers in the first implementation.
- Making Ollama mandatory for all future versions.
- Treating `gemma4:12b` as an approved official source.
- Declaring `embeddinggemma` supported before benchmark evidence and human approval.
- Production corpus crawling, source-review automation, or knowledge-release publication.
- Final production chunking and ranking thresholds.
- Final evaluation-release thresholds owned by the later evaluation ticket.
- Desktop packaging, background-service installation, or automatic application-code updates.
- Verifying macOS or native Linux support without running the required integration matrix.
- User accounts, cloud history, personal eligibility assessment, legal advice, or user uploads.
- Reusing the throwaway prototype implementation.

## Further Notes

- The approved local model was inspected before this PRD: `gemma4:12b` reports 11.9B parameters, Q4_K_M quantization, a 262,144-token model context limit, and completion capability. Product context defaults remain a later evaluation decision and must not simply use the maximum.
- The current development host reports an Intel Core i5-14400F-class CPU and approximately 16 GB RAM. GPU access was not visible inside the Ubuntu environment during inspection, so GPU behavior must be recorded by the live host integration rather than inferred.
- Ollama's official API documents JSON-schema output through `/api/chat` and vector generation through `/api/embed`. These capabilities are used only at the public integration seams.
- Issue #2 is a human decision ticket. The user has approved Ollama and `gemma4:12b`; the resulting runtime baseline still remains provider-neutral.
- Issue #3 is an AFK benchmark ticket whose recommendation feeds the separate human approval ticket for production retrieval architecture.
- The existing `.agent` documentation predates this product and contains unrelated material. This PRD is authoritative only for its stated scope; stale unrelated `.agent` files should not be treated as Danish Immigration RAG requirements.
