# Requalify per release, not per commit

Qualification evidence under `docs/progress/issue-51-*.json` binds sha256 hashes of individual implementation files (`answer_pipeline.py`, `retrieval.py`, `answer_verification.py`, `final_answer_evaluation.py`, and the evidence modules themselves). We accept that refactors change those bytes and make the committed evidence stale: it records what was qualified for that knowledge release candidate, not the current tree. The next release candidate is requalified as a whole. Production modules are therefore not frozen to protect their hashes (the reason `tests/fixture_clock.py` monkeypatched instead of injecting a clock).

## Considered options

- Bind evidence to a package-wide identity (tree hash + contract version) instead of per-file hashes. Deferred, not rejected; revisit if requalification cost becomes the bottleneck.
- Avoid touching hashed files. Rejected: it blocks deepening the hottest modules.
