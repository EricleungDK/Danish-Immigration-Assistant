# Issue 47 Multi-Intent Retrieval

**Recorded:** 2026-07-29

**Status:** Implemented and verified locally through the production retrieval and
local answer-path seams.

## Outcome

Questions may produce alternative topic-tag groups, one for each detected intent.
Tags within a group remain conjunctive. Approval state, source health, language,
and the other source-eligibility checks remain fail-closed before ranking and
result selection.

Each lexical and dense channel reserves the highest-ranked eligible document
for every detected intent before applying its candidate cap. After
reciprocal-rank fusion, final result selection repeats that reservation before
filling the remaining capacity in fused-rank order. This prevents stronger
candidates for one intent from crowding all evidence for another intent out
before or after fusion.

## Deterministic Regression

The public `HybridRetriever.retrieve()` regression builds 22 eligible fixture
documents: 21 permanent-residence candidates that outrank one registration
candidate. Before the result-selection fix, this command failed because the
registration document was excluded by both 20-candidate channel caps and was
therefore absent from all three returned results:

```bash
.venv/bin/python -B -m unittest \
  tests.test_issue_47_multi_intent_answers.Issue47MultiIntentAnswerTests.test_result_limit_keeps_evidence_for_each_detected_intent \
  -v
```

The same test passes after the fix while preserving `limit=3`.

The public `POST /ask` regression also passes. Both
`di-rag-doc-permanent-residence-language` and
`di-rag-doc-registration-deadlines-2026` reach the configured generation
boundary, and the rendered answer contains supported facts and claim-adjacent
citations for both intents.

## Verification

- Issue #47 test module: `2` passed.
- Focused answer-path, evidence-safety, exam-coverage, freshness, and issue #47
  modules: `36` passed.
- Full Python discovery with loopback permission: all `265` non-live tests
  passed and `2` explicit live-only gates skipped.
- An earlier full browser run passed all `30` non-live tests with the explicit
  live Ollama journey skipped. The final full browser replay passed `29`,
  skipped the live journey, and exposed an intermittent focus assertion in the
  user's separate unstaged browser/UI work. A three-run targeted replay passed
  once and failed twice. Its helper waits for an already-visible `.turn` instead
  of a newly added turn, so it can inspect focus before the final HTMX swap.
  Issue #47 changes no browser or focus-management files; that separate work was
  preserved rather than changed in this retrieval fix.
- Python compile validation passed with `PYTHONPYCACHEPREFIX` directed to
  `/tmp/issue47-pycache`; repository `__pycache__` directories are read-only in
  this environment.
- `git diff --check` passed for the issue implementation, tests, and
  documentation.

The repository does not configure a Python type checker or an npm `typecheck`
script, so no separate type-check command is available.

## Boundaries

The deterministic corpus remains project-authored fixture content. These results
verify retrieval selection, source-eligibility preservation, and the local
answer path; they do not qualify the corpus as human-reviewed production
evidence or satisfy the explicit live-model release gates.
