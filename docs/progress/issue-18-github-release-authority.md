# Issue 18: GitHub Release Authority Audit

GitHub issue: https://github.com/EricleungDK/Danish-Immigration-Assistant/issues/18

Status: transport and production orchestration implemented; no production knowledge
release has been published.

## Audit Result

The earlier issue #18 application flow discovered knowledge releases by scanning a
local release-catalogue directory. That flow remains available only when explicitly
injected for fixtures and monitors; the production default now uses GitHub Releases as
the initial publication authority.

`danish_rag.github_release_client.GitHubReleaseClient` supplies the external transport
boundary without changing the local-only answer path. It:

- sends a bounded, content-free request to the public GitHub Releases endpoint for
  `EricleungDK/Danish-Immigration-Assistant`;
- returns published, non-prerelease metadata without exposing the free-form release
  body or downloading assets;
- requires an explicit approval record bound to the requested knowledge release,
  GitHub asset ID, and artifact name before artifact retrieval;
- limits metadata to 2 MiB, artifacts to 256 MiB, one release page to 30 entries,
  release assets to 100 entries, and redirects to three;
- applies one configured total deadline, capped at 30 seconds, across the initial
  connection, redirect-body reads, redirect connections, and response reads. Every
  blocking open or read receives only the remaining time, and an exhausted budget
  fails closed before another blocking operation begins;
- restricts metadata and artifact requests to GitHub-controlled HTTPS origins;
- converts malformed URL authorities, including invalid or out-of-range ports from
  metadata, final response URLs, redirects, or transport errors, into the client domain
  error instead of leaking parser exceptions;
- checks advertised byte counts and optional GitHub SHA-256 digests, writes through a
  unique local partial file, refuses overwrite, atomically links the completed file into
  place, cleans the partial file on every exit path, and never unpacks or activates
  downloaded bytes.

These checks supplement, but do not replace, the project trust root, detached
manifest-signature verification, manifest artifact hashes, schema compatibility, or
atomic knowledge-release installation.

## Production Integration

The GitHub list response cannot supply compatibility, reviewed-source changes, and
expected-indexing work because those facts live in the signed knowledge release. The
implemented application flow therefore keeps three explicit user-visible actions:

1. A user-requested check lists content-free GitHub release metadata and locally
   identifies a newer knowledge-release tag and its single expected `<tag>.zip` asset.
   It does not download the archive.
2. A separate user action approves the exact tag, GitHub asset ID, and filename. The
   application downloads into temporary local staging, rejects traversal, symbolic
   links, special files, duplicate paths, excessive members, and expanded-size
   overflow, verifies the project signature and manifest, confirms the manifest
   release ID matches the approved GitHub tag, and then shows the signed review summary.
3. A later explicit install action re-verifies the staged release, rebuilds the local
   index, and atomically activates it. Download and verification never auto-install.

The local release catalogue remains an explicitly injected fixture/test channel, not
the production publication authority. Production app construction uses GitHub Releases
by default. Release-network monitoring instruments the default client's
`OpenerDirector.open` path with in-memory responses: it observes content-free metadata
discovery and approval-bound artifact retrieval, and separately proves an unapproved
artifact request is blocked before a response can be returned. No real GitHub artifact
is downloaded by that monitor.

No GitHub release was created, uploaded, or represented as a reviewed production
knowledge release by this implementation.

## Verification

These counts record the issue #18 implementation state. Later automatic-check and
installation-progress UI changes make the Playwright result historical; the current
browser/accessibility suite must be rerun before release qualification.

- Focused update/client/install/monitor/app regressions: 38 passed.
- GitHub update orchestration and hostile-archive cases: 8 passed.
- Default GitHub client transport and boundary checks: 14 passed.
- Release privacy, rollback, and environment monitor tests: 4 passed.
- Historical Playwright browser and automated accessibility suite: 15 passed.

The live strict release-monitor evidence must be regenerated after the final code state
is settled so its recorded workflow hashes cover this integration.
