# Source Governance And Knowledge-Release Integrity

This document records the issue #5 operating-model recommendation and the issue #6 architecture approval for the human-reviewed source registry and knowledge-release lifecycle in Danish Immigration RAG. It is a governance baseline, not an implementation mandate for every future automation detail.

## Approval Status

Issue #6 approves this baseline for the MVP architecture gate:

- The lifecycle and threat model are accepted as compatible with the PRD safety boundaries.
- Human review and publication authority are assigned to maintainer roles. A production knowledge release must name the human maintainer or maintainers acting as source curator, source reviewer, release operator, release approver, and recovery owner.
- The selected integrity approach is a signed release manifest containing SHA-256 artifact hashes and a documented project trust root.
- Source blocking, warning, withdrawal, and emergency recovery rules follow the state model and recovery procedures below.
- Release implementation tooling, exact signing commands, final release thresholds, and Fresh Tomato Score algorithms remain separate implementation decisions.

## Recommendation

Use a maintainer-owned source registry with explicit source states, two-person review for material changes, signed release manifests, and atomic user-approved installation.

The preferred baseline is:

- A source may support answers only when it is `approved-current` or explicitly `overdue-policy-usable`.
- Changed, fetch-failed, broken, redirected, extraction-failed, overdue-blocked, withdrawn, superseded, and unapproved sources are blocked from supporting answers until the allowed review transition restores eligibility.
- Every knowledge release ships a manifest containing source provenance, content hashes, review state, schema version, corpus identity, application compatibility, and release integrity metadata.
- GitHub Releases remains the initial publication authority, with manifest signature verification before local installation.
- Maintainers separate source review from release publication where staffing allows; a single maintainer may operate the MVP only with visible audit notes and post-release review.

## Evidence Basis

This recommendation is grounded in existing project constraints:

- The runtime baseline separates release-network activity from the local-only answer path, requires explicit user approval for knowledge release installation, and forbids `git pull` as an update mechanism.
- The retrieval benchmark fixtures already treat `changed-unreviewed`, `broken`, and `extraction-failed` sources as ineligible for retrieval credit while allowing policy-visible overdue sources only when explicitly usable.
- The architecture direction already makes GitHub Releases the first publication authority and requires release integrity verification plus atomic installation rollback.
- The threat model below follows from the project-owned source registry: if maintainers publish the trusted corpus, the application must verify both artifact integrity and maintainer-controlled release authority before a source can support answers.

## Source States

| State | Meaning | May support answers? | Typical owner |
| --- | --- | --- | --- |
| `discovered` | Candidate official source found but not reviewed. | No | Source curator |
| `candidate-approved-url` | URL and publisher are approved for monitoring, but content is not in a release. | No | Source curator |
| `fetch-failed` | The approved URL could not be fetched after retry policy. | No for new releases; previous installed release remains usable until superseded or withdrawn. | Release operator |
| `broken` | The source is reachable but no longer provides usable official content, such as an error page, unrelated page, or publisher-side removal. | No | Source reviewer |
| `redirected-pending-review` | The URL redirects to a new location that has not been reviewed. | No | Source curator |
| `extraction-failed` | Fetch succeeded but normalization or chunk extraction failed. | No | Release operator |
| `changed-unreviewed` | Fetched content differs from the last reviewed content. | No | Source reviewer |
| `approved-current` | Content, provenance, and normalized extraction have been reviewed and approved. | Yes | Source reviewer |
| `overdue-policy-usable` | No known content change or health failure, but the scheduled review is overdue and policy permits temporary use. | Yes, visibly stale. | Source reviewer |
| `overdue-blocked` | Scheduled review is overdue beyond the policy grace period. | No | Source reviewer |
| `withdrawn` | Maintainers decided the source must not support answers. | No | Release approver |
| `superseded` | Replaced by a newer approved source or URL. | No in new releases; historical answers keep provenance. | Source reviewer |

## Lifecycle

### Discovery

Discovery records the official publisher, URL, topic, language, reason for inclusion, initial owner, and evidence that the source is within product scope. A discovered source cannot enter the corpus until a curator confirms it is an approved official source candidate.

Allowed transitions:

- `discovered` -> `candidate-approved-url` after curator approval.
- `discovered` -> `withdrawn` when out of scope, duplicate, unofficial, or legally risky.

Blocked transitions:

- `discovered` -> `approved-current` without content review.
- `discovered` -> published release eligibility.

### Fetch And Change Detection

Automation fetches only approved candidate or already approved URLs. It records status code, final URL, retrieval timestamp, content hash, extraction hash, source headers where useful, and failure diagnostics.

Allowed transitions:

- `candidate-approved-url` -> `changed-unreviewed` after the first successful fetch and extraction.
- `approved-current` -> `changed-unreviewed` when source content or extracted content hash changes.
- `approved-current` -> `fetch-failed` after retry policy fails.
- `approved-current` -> `broken` when fetch succeeds but the page no longer contains usable official content.
- `approved-current` -> `redirected-pending-review` when the final URL changes materially.
- `approved-current` -> `extraction-failed` when normalization fails.
- `approved-current` -> `overdue-policy-usable` when the scheduled review date passes but policy grace remains.
- `overdue-policy-usable` -> `overdue-blocked` when grace expires.

Blocked transitions:

- `changed-unreviewed`, `fetch-failed`, `broken`, `redirected-pending-review`, `extraction-failed`, and `overdue-blocked` cannot move directly to release eligibility.
- Automation cannot mark a changed source as `approved-current`.
- Redirects cannot be silently accepted as the same approved source.

### Human Review And Approval

Reviewers compare fetched content against the previous approved version and the normalized extraction. They record the reviewer, review timestamp, decision, materiality, notes, and any interpretation risk. Material changes should receive a second reviewer before publication.

Allowed transitions:

- `changed-unreviewed` -> `approved-current` after content and extraction review.
- `redirected-pending-review` -> `candidate-approved-url` when the new URL is accepted as the monitored location.
- `redirected-pending-review` -> `withdrawn` when the redirect breaks provenance or moves out of scope.
- `extraction-failed` -> `changed-unreviewed` after extraction is repaired and review is still required.
- `fetch-failed` -> `approved-current` only when the original content is fetched again and matches the last approved hashes.
- `broken` -> `changed-unreviewed` when usable official content returns but requires review.
- `broken` -> `withdrawn` when the source is removed, no longer official, or no longer in scope.
- `overdue-policy-usable` -> `approved-current` after scheduled review confirms the source.
- `overdue-blocked` -> `approved-current` after scheduled review confirms the source, refreshes the check timestamp, and sets the next review date.
- `overdue-blocked` -> `withdrawn` when review cannot confirm the source remains official, in scope, and usable.
- Any active state -> `withdrawn` when the source is no longer trusted, official, in scope, or safe to publish.

Blocked transitions:

- `fetch-failed` -> `approved-current` based only on a cached copy.
- `broken` -> `approved-current` without fresh content and extraction review.
- `extraction-failed` -> `approved-current` without reviewing the repaired extraction.
- `overdue-blocked` -> `overdue-policy-usable` without an explicit reviewer decision and new review date.

### Publication

Publication assembles only eligible sources into a knowledge release. The release operator verifies manifest completeness, schema compatibility, source eligibility, artifact hashes, and signature status before publishing.

Allowed transitions:

- Eligible `approved-current` and policy-allowed `overdue-policy-usable` sources -> included in a release manifest.
- `approved-current` -> `superseded` when a replacement approved source is published.
- Published release -> withdrawn release when a material source is later invalidated.

Blocked transitions:

- A release cannot include `discovered`, `candidate-approved-url`, `changed-unreviewed`, `fetch-failed`, `broken`, `redirected-pending-review`, `extraction-failed`, `overdue-blocked`, `withdrawn`, or `superseded` as answer-supporting material sources.
- A release cannot lower `minimum_application_version` below the application features required by its schema.
- A release cannot be installed if manifest verification fails.

### Installation And Withdrawal

The application may check for knowledge releases, but installation requires explicit user approval and must remain separate from application-code updates. Installation verifies the manifest, artifact hashes, schema compatibility, minimum application version, corpus identity, and signature status before swapping the local corpus atomically.

Allowed transitions:

- Verified release artifact -> staged local installation.
- Staged installation -> active corpus after hash, schema, and compatibility checks pass.
- Active corpus -> previous active corpus when installation fails before activation.
- Active corpus -> withdrawn locally when maintainers publish a withdrawal notice that matches the installed release identity.

Blocked transitions:

- The application cannot install a knowledge release through `git pull`.
- The application cannot mix documents from different release manifests into one active corpus.
- The application cannot keep using a withdrawn release without surfacing a blocking trust warning.

## Compatibility Rules

Knowledge-release compatibility is evaluated before installation:

- `manifest_schema_version` must be in the application's supported manifest schema range. Unknown major versions are blocked; unknown minor versions are allowed only when the manifest declares backward-compatible fields.
- `corpus_schema_version` must be supported by the installed application. A release that changes document shape, source-state semantics, chunk metadata, citation metadata, or trust-indicator inputs must bump the corpus schema version.
- `minimum_application_version` is the lowest application version that understands the manifest, corpus schema, source-state eligibility rules, and installation checks required by the release. Older applications must refuse installation with an upgrade message.
- A knowledge release may require a higher `minimum_application_version`, but it may not lower the requirement below the version needed by its schema or source-state semantics.
- Downgrades are allowed only to a previously verified release whose schema remains supported and whose release ID is not withdrawn.
- Local retrieval indexes are derived artifacts. If the corpus ID, corpus schema version, embedding model identity, vector dimensions, or dense-index schema version changes, the application must rebuild the local index instead of reusing incompatible vectors.

## Release Manifest

Each knowledge release should include a manifest with these fields:

```json
{
  "manifest_schema_version": "1.0",
  "knowledge_release_id": "kr-YYYY-MM-DD.N",
  "created_at_utc": "YYYY-MM-DDTHH:MM:SSZ",
  "minimum_application_version": "0.1.0",
  "corpus_schema_version": "1.0",
  "corpus_id": "sha256:<canonical manifest content hash>",
  "source_registry_version": "sr-YYYY-MM-DD.N",
  "sources": [
    {
      "source_id": "nyidanmark-permanent-residence-language-requirements",
      "publisher": "SIRI",
      "official_url": "https://example.invalid/source",
      "final_url": "https://example.invalid/source",
      "topic": "permanent-residence language requirements",
      "language": "da",
      "review_state": "approved-current",
      "reviewed_at_utc": "YYYY-MM-DDTHH:MM:SSZ",
      "reviewers": ["reviewer-id"],
      "last_checked_at_utc": "YYYY-MM-DDTHH:MM:SSZ",
      "source_content_sha256": "<hex>",
      "normalized_document_sha256": "<hex>",
      "extraction_schema_version": "1.0",
      "fresh_tomato_inputs": {
        "next_review_due_utc": "YYYY-MM-DDTHH:MM:SSZ",
        "source_health": "current"
      }
    }
  ],
  "artifacts": [
    {
      "path": "corpus/documents.jsonl",
      "sha256": "<hex>",
      "bytes": 12345
    }
  ],
  "integrity": {
    "hash_algorithm": "sha256",
    "signature_algorithm": "minisign-or-sigstore",
    "signature": "<detached signature reference>",
    "trust_root_id": "project-maintainer-release-key-v1"
  }
}
```

The manifest is the installation contract. Retrieval indexes are local derived data and should record compatibility with this manifest rather than become the authoritative source of provenance.

## Integrity And Trust-Root Options

Threat model:

- An attacker may tamper with release artifacts after publication.
- An attacker may compromise a maintainer account or automation token.
- A source website may change content between reviews.
- A local installation may be interrupted during download, verification, or indexing.
- The product must preserve the local-only answer path and cannot send conversation content for trust verification.

Operational model:

- GitHub Releases is the initial distribution channel.
- Maintainers are few, so the baseline cannot require a large ceremony for every small release.
- Users need a clear install-or-block decision, not a detailed security workflow.

Compared options:

| Option | Strengths | Weaknesses | Recommendation |
| --- | --- | --- | --- |
| Manifest hashes only | Simple and detects accidental corruption. | Does not protect against release-channel tampering because attacker can replace both artifact and manifest. | Use inside signed manifests, not alone. |
| GitHub release checksums plus maintainer review | Fits current tooling and audit trail. | Still relies heavily on GitHub account security. | Acceptable only as a temporary pre-signing MVP step. |
| Detached project signing key | Strong offline verification, simple local install check, no remote trust lookup. | Requires key custody, rotation, and recovery process. | Preferred baseline for MVP approval. |
| Sigstore keyless signing | Good transparency log and less long-lived key custody. | More operational complexity and external-service assumptions. | Good later option; evaluate when release automation matures. |
| Threshold signatures | Best protection against single maintainer compromise. | Heavy for a small early project. | Defer until release volume or risk justifies it. |

Preferred baseline: publish a signed manifest with SHA-256 hashes for every artifact. Keep the first trust root in the application or in a separately verified project configuration. Document key rotation, revoked key IDs, and emergency withdrawal handling before the first public knowledge release.

## Maintainer Roles

| Role | Responsibilities | Separation rule |
| --- | --- | --- |
| Source curator | Admits official source candidates and records scope rationale. | Should not be the only approver for material content changes they introduced. |
| Source reviewer | Reviews changed content and normalized extraction. | Should be separate from release operator for material changes where staffing allows. |
| Release operator | Builds artifacts, checks manifest completeness, signs or requests signing, and publishes. | Should not publish unreconciled review failures. |
| Release approver | Confirms the release is eligible for publication and handles withdrawal decisions. | Should be separate from the operator for non-trivial releases. |
| Recovery owner | Rotates signing keys, publishes withdrawal notices, and coordinates rollback guidance. | May be the same person in MVP, but actions must be auditable. |

MVP staffing fallback: one maintainer may perform multiple roles only when the release notes state the reduced separation and the release is reviewed after publication by a second maintainer before it becomes the recommended release.

## Recovery Procedures

Broken source:

- Mark the source `fetch-failed`, `broken`, `redirected-pending-review`, or `extraction-failed`.
- Block it from new release eligibility.
- Preserve the last active installed corpus unless a withdrawal notice says the source is unsafe.

Material source error after publication:

- Publish a withdrawal notice naming the release ID, source IDs, reason, and replacement guidance.
- Remove or supersede the affected source in the next release.
- Application surfaces a blocking trust warning for installed withdrawn releases.

Maintainer account or automation token compromise:

- Freeze publication until maintainers identify the affected credentials, release IDs, and source registry changes.
- Revoke compromised tokens and rotate affected maintainer access.
- Audit releases and registry changes made during the suspected compromise window.
- Withdraw releases that cannot be proven clean, then republish the latest good reviewed corpus through trusted credentials and signing keys.

Signing key compromise:

- Publish revocation notice through all available project channels.
- Remove the compromised trust root from the next application release.
- Re-sign the latest good knowledge release with the replacement key after human review.

Failed local installation:

- Keep the previous active corpus and index.
- Delete the failed staged install.
- Show the failing verification or compatibility check.

## Preferred Baseline Summary

Issue #5 should approve the governance model, not every future implementation detail. The baseline to carry forward is:

- Human-reviewed source registry owned by maintainers.
- Explicit blocked states for unapproved, changed, fetch-failed, broken, redirected, extraction-failed, overdue-blocked, withdrawn, and superseded sources.
- Policy-visible `overdue-policy-usable` state for temporary use when a source is stale but unchanged and still within grace.
- Signed release manifest with provenance, hashes, review state, schema version, and minimum application compatibility.
- Atomic explicit-user-approved installation with rollback.
- Documented recovery paths for bad sources, bad releases, key compromise, and failed local installs.
