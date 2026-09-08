# Local worktree cleanup

Reviewed 2026-09-08. Python bytecode was accidentally tracked despite the existing `__pycache__/` and `*.pyc` ignore rules. Nine bytecode files were removed from Git's index; local copies remain available and Python regenerates them as needed. No source code, review packet, signing key or backup was deleted.

Local release exports and discovery snapshots now have scoped `.gitignore` rules. The entire `review/` directory is not ignored: existing tracked tools and teaching material remain tracked, and unrelated new tools remain visible.

## Optional deletion list

These files are no longer needed for release qualification. Keep them if convenient; deleting them only removes local reference material.

| Local path under `review/` | Why optional |
| --- | --- |
| `release-test-updates.patch` | Already applied and committed. |
| `release-benchmark-preview.json` | Exploratory results superseded by committed `docs/progress/issue-51-approved-retrieval.json`. |
| `release-benchmark-proposal.json` | Verified byte-identical to `data/evaluation/production-retrieval-v1.json`. |
| `release-human-decisions.json` | Verified byte-identical to `docs/progress/issue-51-production-retrieval-approval.json`. |
| `release-human-review.html` | Completed benchmark decision form. |
| `release-final-review.html`, `release-candidate-review.md`, `release-review-checklist.md` | Convenient local summaries; authoritative approval and qualification are committed under `docs/progress/` and `config/`. |
| `missing-source-investigation.html`, `official-source-discovery-20260908/` | Optional online-discovery notes and raw snapshots, never admitted to the production corpus; about 268 KB for snapshots. Removing them breaks local investigation links but does not affect the app or release. |

## Keep locally and ignored

- `review/private-evaluation/`: original answer/review packets and private receipts. Preserve these for audit and reproducibility; public aggregate reports do not replace the original packets.
- `review/issue-46-official-source-review/`: original source-review workspace, including snapshots and review material.
- `review/signing-key-backup.py`, `Create signing backup.cmd`, `Verify signing backup.cmd`: useful owner-specific recovery helpers. Their local paths make them unsuitable as general repository tooling in their current form. The scripts are not the signing key or the encrypted backup.
- The actual signing key outside this repository and its encrypted cloud backup: retain both. Cleanup does not touch them.

## Generated files

`__pycache__/`, `*.pyc`, `playwright-report/`, and `test-results/` can be deleted when not in use. They are already ignored. `.venv/` and `node_modules/` are also ignored, but removing them requires reinstalling dependencies before using the app or tests.

Do not delete committed candidate evidence, approval records, signed releases or source registries merely because a similarly named local export exists. Do not use `git clean -fdx`: it would also delete ignored private review packets and installed dependencies.
