# Issue 5 Source Governance Progress

GitHub issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/5

Parent PRD issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/1

## Trace

- Recommendation: [docs/source-governance.md](../source-governance.md)
- Architecture summary: [docs/architecture.md](../architecture.md)
- Runtime release-network boundary: [docs/runtime-baseline.md](../runtime-baseline.md)
- Domain vocabulary: [CONTEXT.md](../../CONTEXT.md)

## TDD Record

This issue is a governance recommendation deliverable, not executable application behavior. No TDD seam was available. Verification is documentation review plus the existing Python test suite to ensure adjacent checked runtime and retrieval contracts still pass.

## Acceptance Criteria Mapping

- Lifecycle covers discovery, fetch, change detection, human review, approval, publication, installation eligibility, and withdrawal: [docs/source-governance.md](../source-governance.md), "Lifecycle".
- Changed, broken, redirected, extraction-failed, overdue, and unapproved sources have explicit allowed and blocked transitions: [docs/source-governance.md](../source-governance.md), "Source States" and "Lifecycle".
- Release manifest includes provenance, hashes, review state, schema version, and minimum application compatibility: [docs/source-governance.md](../source-governance.md), "Release Manifest".
- Integrity/signing and trust-root options are compared using a documented threat and operational model: [docs/source-governance.md](../source-governance.md), "Integrity And Trust-Root Options".
- Maintainer roles, separation of duties, recovery procedures, and a preferred baseline are documented: [docs/source-governance.md](../source-governance.md), "Maintainer Roles", "Recovery Procedures", and "Preferred Baseline Summary".

## Recommendation

Adopt the signed-manifest, maintainer-reviewed source registry baseline described in [docs/source-governance.md](../source-governance.md) for the MVP architecture approval gate.

## Remaining Limitations

- This document does not implement registry storage, release tooling, signing commands, or application installation behavior.
- Exact signing technology remains an implementation decision between detached project signing and a later Sigstore evaluation.
- Fresh Tomato Score algorithms remain separate from this governance lifecycle, though the manifest records the inputs needed by that later scoring work.
