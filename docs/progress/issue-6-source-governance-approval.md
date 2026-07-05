# Issue 6 Source Governance Approval

GitHub issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/6

Parent PRD issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/1

Blocked by: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/5

## Trace

- Approved governance baseline: [docs/source-governance.md](../source-governance.md)
- Architecture decision summary: [docs/architecture.md](../architecture.md)
- Recommendation record from issue #5: [docs/progress/issue-5-source-governance.md](issue-5-source-governance.md)
- Runtime release-network boundary: [docs/runtime-baseline.md](../runtime-baseline.md)
- Domain vocabulary: [CONTEXT.md](../../CONTEXT.md)

## TDD Record

This issue is a human architecture approval gate, not executable application behavior. No TDD seam was available. Verification is documentation review plus the existing Python test suite to ensure adjacent runtime and retrieval contracts still pass.

## Approved Decisions

- The issue #5 lifecycle and threat model are accepted against the PRD safety boundaries for approved official sources, local-only answers, explicit-user-approved knowledge releases, and no answer-time browsing.
- Maintainer-owned source registry authority is approved. Human source review, release approval, release publication, and recovery authority are assigned to the maintainer roles documented in [docs/source-governance.md](../source-governance.md).
- A production knowledge release must record the named human maintainer or maintainers acting as source curator, source reviewer, release operator, release approver, and recovery owner. The MVP fallback permits one maintainer to hold multiple roles only with visible audit notes and post-release review.
- The selected release-integrity approach is a signed release manifest with SHA-256 artifact hashes and a documented project trust root. Hash-only manifests are allowed only as a temporary pre-signing MVP step.
- Source blocking, warning, withdrawal, and emergency recovery rules are approved as documented in the source-state lifecycle and recovery procedures.

## Acceptance Criteria Mapping

- Lifecycle and threat model reviewed against PRD safety boundaries: [docs/source-governance.md](../source-governance.md), "Approval Status", "Evidence Basis", "Lifecycle", and "Integrity And Trust-Root Options".
- Human responsibilities and approval authority assigned explicitly: [docs/source-governance.md](../source-governance.md), "Approval Status" and "Maintainer Roles"; [docs/architecture.md](../architecture.md), "Source Governance And Updates".
- Release-integrity and trust-root approach selected: [docs/source-governance.md](../source-governance.md), "Approval Status" and "Integrity And Trust-Root Options"; [docs/architecture.md](../architecture.md), "Source Governance And Updates".
- Source blocking, warning, withdrawal, and emergency recovery rules approved: [docs/source-governance.md](../source-governance.md), "Source States", "Lifecycle", and "Recovery Procedures".
- Approved decisions recorded in architecture documentation: [docs/architecture.md](../architecture.md), "Scope And Traceability" and "Source Governance And Updates".

## Remaining Limitations

- This approval does not implement source-registry storage, release tooling, signing commands, application installation behavior, or withdrawal-notice handling.
- Exact signing technology, key custody procedure, and key-rotation commands remain implementation details under the approved signed-manifest and project-trust-root baseline.
- Fresh Tomato Score algorithms remain separate from this governance decision, though the release manifest records the inputs needed by that later scoring work.
