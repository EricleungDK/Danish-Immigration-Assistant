# Issue 48 grounded-flexibility qualification

**Status:** deterministic qualification passed on 2026-07-30

## Evidence

- Reviewed synthetic case contract:
  [`data/evaluation/grounded-flexibility-v0.1-candidate.json`](../../data/evaluation/grounded-flexibility-v0.1-candidate.json)
- Evaluator:
  [`danish_rag/grounded_flexibility_evaluation.py`](../../danish_rag/grounded_flexibility_evaluation.py)
- Machine-readable qualification evidence:
  [`docs/progress/issue-48-grounded-flexibility.json`](issue-48-grounded-flexibility.json)
- Public-seam regression tests:
  [`tests/test_issue_48_grounded_flexibility.py`](../../tests/test_issue_48_grounded_flexibility.py)
- Review authority: [GitHub issue 48](https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/48)

The case set contains nine project-authored synthetic questions. It uses no
production-user questions, conversations, or answers. The selected permanent-
residence-language and PD3-registration intents each have three pairwise
low-word-overlap phrasings. Two additional questions combine both intents, and
one answers the supported permanent-residence part while refusing a personal
eligibility conclusion.

## Deterministic results

The production `HybridRetriever` and `AnswerService` adapter ran every case over
the bundled reviewed knowledge release. Generation was replaced only at the
external provider boundary with a deterministic fixture restricted to the two
material sources under evaluation.

| Metric | Result | Threshold | Status |
| --- | ---: | ---: | --- |
| Permanent-residence intent evidence coverage | 6/6 (1.0) | 1.0 | Passed |
| PD3-registration intent evidence coverage | 5/5 (1.0) | 1.0 | Passed |
| Required-fact coverage | 16/16 (1.0) | 1.0 | Passed |
| Citation correctness | 11/11 (1.0) | 1.0 | Passed |
| Unsupported-claim rate | 0/11 (0.0) | 0.0 maximum | Passed |
| Refusal precision | 1/1 (1.0) | 1.0 | Passed |
| Paraphrase consistency | 3/3 groups (1.0) | 1.0 | Passed |
| Claim-adjacent citation coverage | 11/11 (1.0) | 1.0 | Passed |

Paraphrase consistency compares only the supported fact-ID set and material
citation-ID set for each group. Generated wording, summaries, and section prose
are not compared for identity.

## Fail-closed regression

The regression removes the PD3 registration document from one composite
execution. The case then records the missing document against the registration
intent, fails that case, lowers only the affected intent coverage, and makes the
strict report fail. A plausible answer for the other intent cannot mask the
missing material source.

Additional regressions prove that citing the right document does not credit an
unstated required fact, an unrelated refusal does not count as a scoped refusal,
and an unevaluable execution cannot disappear inside aggregate citation or
unsupported-claim metrics. Material-source eligibility reuses the production
Fresh Tomato assessment, including the policy-usable overdue state.

## Verification

```bash
.venv/bin/python -B -m unittest tests.test_issue_48_grounded_flexibility -v
.venv/bin/python -B -m unittest discover -v
npm run test:browser
```

Results:

- Issue-specific qualification: 9 tests passed.
- Full Python suite: 276 tests passed; 2 explicitly opt-in live-environment
  checks skipped.
- Browser suite: 29 tests passed and the live-Ollama journey skipped. The
  evidence-drawer evaluation saw one unrelated automatic loopback update-status
  request during the batch run; its isolated retry passed (1/1).
