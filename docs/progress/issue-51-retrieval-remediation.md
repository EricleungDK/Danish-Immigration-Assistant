# Issue #51 — Retrieval remediation

The production-candidate nine-case grounded-flexibility suite now retrieves all
required source documents at three: permanent-residence coverage **6/6**, and
registration coverage **5/5**, with no execution errors or blocked-source results.
This is retrieval evidence; answer semantics and human review remain separate.

## Diagnosis and change

The two misses belonged to one compound paraphrase. Bare `residence` and
`enrolment` were not recognized as separate retrieval subjects, so the question
lost both intent reservations. Restoring those reservations fixed registration,
but permanent-residence coverage still failed: the primary requirements source
and both examination-equivalence sources share the same broad topic tags, and
the equivalence chunks ranked above the primary source. Bilingual synonym
expansion alone did not fix that remaining miss.

Semantic-chunk retrieval now adds a source-title field to reciprocal-rank fusion,
retaining `k=60`. Actual question/title token overlap is weighted by inverse
source frequency; common function words are excluded. Each source contributes
one title vote through its strongest already-retrieved chunk. Title relevance
therefore survives long source bodies without receiving extra votes for repeated
chunks. Eligibility filtering, per-intent reservations, and source diversity
still apply. Whole-document retrieval retains its existing two channels.

Word-boundary matching recognizes residence and British/American enrolment forms.
No source IDs, evaluation case IDs, or exact evaluation prompts are embedded in
the ranking algorithm. Approved corpus bytes, index bytes, datasets, and thresholds
are unchanged. The content-free collector now binds both retrieval and collector
implementation SHA-256 hashes as well as the signed manifest and dense index.

## Passage-level follow-up

Source-document coverage was insufficient: a returned primary-source chunk
initially discussed civic participation or general eligibility rather than the
language requirement. A fresh live audit of all factual cases exposed that gap
before final answer capture. The fixes below preserve the original question for
answer generation and apply only to retrieval:

- Compositional normalization supplies Danish examination terms for English
  `Danish language test` and generic examination phrasing, and registration terms
  for British/American enrolment forms.
- Lexical matching removes English function words, retains single-digit exam
  levels, and bounds the remaining unique terms at 48. Translated subject terms
  therefore survive longer compound questions.
- Within an already-selected source, passage bodies mentioning an examination
  explicitly named in the actual question precede unrelated chunks. Bare years,
  levels, dates, and generated expansion terms do not create named entities.
  Explicit broad Danish-examination requests prefer bodies covering more named
  examinations. Explicit individual exam requests require a match without
  rewarding unrelated lists merely for repeating more names.
- After topic reservations, remaining slots cover named examinations absent from
  selected passage bodies before adding redundant sources. Comparison questions
  therefore retain the approved overview with PD1 and Studieprøven evidence.

Fresh live passage checks find the basic language-requirement text in all six
permanent-residence cases and the exact signup-location instruction in all five
registration cases, including both compound questions. The final-answer
comparison case now retrieves the approved overview, and the broad requirement
case retrieves basic and supplemental language evidence. These are passage
availability checks, not independent semantic adjudications of generated answers.

## Verification

- Three independent compound paraphrases failed under ranking pressure before
  the fix and pass afterward through the real production retriever. Additional
  real-corpus tests assert exact requirement and signup passages and overview
  statements; comparison and language-test regressions were observed failing
  before their respective fixes.
- Negative tests confirm title matches cannot admit changed-unreviewed, broken,
  extraction-failed, or unapproved sources, even if both input channels return them.
- Title ranking tests verify one vote per source, selection of its best retrieved
  chunk, no votes from unmatched/function words, and stable relevance tie order.
- The unchanged live grounded-flexibility collector passed against the installed
  `kr-2026-09-05.1` candidate and local `embeddinggemma`.
- The original nine-query lexical/dense/hybrid comparison also ran unchanged with
  live local embeddings. All three achieved recall-at-three 1.0, with zero blocked
  or forbidden-source violations. That comparison exercises the historical
  benchmark implementation; the candidate collector and production regression
  tests exercise the changed application retriever.

The final integration handoff should retain the freshly regenerated content-free
collector report. This remediation does not replace independent review of exact
answers or certify that every possible question is bug-free.

## Required live passage regression

```bash
DI_RAG_RUN_LIVE_PASSAGE_RETRIEVAL=1 .venv/bin/python -B -m unittest tests.test_retrieval_passages_live
```

The three live tests check every grounded-flexibility case for its actual required
passages, plus final-answer requirement and comparison cases. They are opt-in
because the real installed candidate and local embedding model are required.
Their success confirms passage availability, not generated-answer correctness.
The deterministic companion test replays content-free real dense rankings while
using the real lexical and final selection code; its compound assertion was
verified to fail against the original retriever and pass against the remediation.
The toy embedding fixture by itself does not model the semantic rankings required
to certify these exact passages.

## Certificate-list context follow-up

The subsequent live answer capture exposed a context-availability gap: a selected
certificate-list tail lacked its earlier introductory list statement. The signed
flattened corpus has chunk ordering but no list membership or heading hierarchy.

For an explicitly detected certificate-equivalence intent, retrieval now treats
an earlier same-source chunk containing a bounded `List of …:` or `Liste over …:`
introduction as an additional context candidate. It remains a separate exact
approved chunk; no content is rewritten, and the heuristic does not assert that
the later passage belongs to that list. It uses remaining result capacity or
replaces redundant source-diversity fill, preserving existing intent reservations
and already-covered explicitly named examinations. The ordinary top-three budget
is unchanged. Plain residence questions do not activate this preference.

This bounded relevance heuristic can reduce source diversity to expose useful
context. Fully explicit list relationships require future corpus metadata; they
are not inferred or manufactured here. The verifier and human reviewer must still
check whether the exact passages support a proposed relationship.

The production certificate-context regression failed before the change and now
passes for three certificate/diploma paraphrases. Negative controls cover absent
introductory colons, ordinary noninitial chunks, blocked/unapproved context, and
named-exam coverage that exhausts the budget. All existing source and live passage
checks pass, including the new separate introduction/tail availability check.
