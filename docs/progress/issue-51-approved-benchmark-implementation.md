# Owner-approved retrieval qualification — 8 September 2026

The owner approved the exact 15-case production proposal, including all-critical mapping, in `issue-51-production-retrieval-approval.json`. The decision explicitly does not approve publication. The embedded proposal remains byte-for-byte equivalent to the reviewed proposal; its draft status is not itself an approval record.

`danish_rag/approved_retrieval_evidence.py` implements two mandatory checks: signed production chunk retrieval (15/15 required-source and critical-case hits), and the unchanged nine-query fixture benchmark against its own corpus (7/7 eligible required-source hits). Both have zero blocked/forbidden-source violations. The historical candidate/fixture mismatch remains 2/7 in its original artifact and in the current evaluator diagnostics.

The evaluator verifies the decision and proposal hashes, measurement provenance, unchanged numerical thresholds, complete critical mapping, exact signed chunks, source eligibility at collection and evaluation, current implementation hashes, and identical inspected embedding model identity/dimensions across both corpora. Fixture safety considers the entire eligible list; Recall@3 considers only its first three entries. The production check measures required-source coverage; accepted answer-level human fact/citation review remains separately mandatory.

No production answer, source document, generation model, signing key, or retrieval implementation was changed. No answers were regenerated. No synthetic document was admitted into production. The nine fixture cases remain unchanged. Existing publication, privacy and manual accessibility controls remain mandatory.

Validation: 42 focused tests passed; full Python suite 502 tests, zero failures/errors, five skips. Tests cover mismatched approval, missing cases, production and fixture misses, rank-four forbidden fixture results, unsigned chunks, changed model/dimensions/implementation, threshold drift, and malformed evidence. Independent Spec and Standards code reviews identified two issues (fixture safety truncation and model provenance); both were fixed and verified by their reviewers, with no remaining blocking findings.

Fresh collection command:

```bash
.venv/bin/python -B -m danish_rag.approved_retrieval_evidence --root . --output docs/progress/issue-51-approved-retrieval.json
```

After a fresh collection, update its hash in `config/release-qualification.json`, then run `python -B -m danish_rag.release_evaluation --strict` in the project environment. A pending final release-owner decision must still produce a nonzero strict result.
