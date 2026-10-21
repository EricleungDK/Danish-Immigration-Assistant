"""Requalify the committed issue-51 evidence against the current implementation (#70).

Per docs/adr/0001-requalify-per-release-not-per-commit.md the committed qualification
evidence binds sha256 hashes of individual implementation files and goes stale when those
files change. It records what was qualified for that release candidate, not the current
tree. These helpers rebuild the same bindings in a temporary copy of the repository (the
committed evidence is never modified), so tests can still prove that the verification
chain passes with intact bindings and fails closed on every other tampering, while a
separate test asserts the committed evidence is rejected as stale.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

_IGNORED = shutil.ignore_patterns(
    ".git", ".venv", "node_modules", "__pycache__", ".claude", "playwright-report",
    "test-results", "*.pyc",
)
_HEX = re.compile(r"^[0-9a-f]{64}$")

# Which implementation file each single-hash provenance field records.
_COLLECTORS = {
    "issue-51-candidate-retrieval-baseline.json": "danish_rag/candidate_retrieval_evidence.py",
}
# Implementation files renamed since the evidence was committed (old path -> current path).
# The rebind follows the rename, so the recorded inventory must still match the code's
# current IMPLEMENTATIONS exactly; nothing is dropped or ignored.
_RENAMED_IMPLEMENTATIONS = {
    "danish_rag/source_freshness.py": "danish_rag/trust_indicators.py",  # #71
}
_DEFAULT_COLLECTOR ="danish_rag/grounded_flexibility_retrieval.py"

# The only evidence files a full rebind may rewrite. A binding outside this set (for
# example an approval record) changing means a new dependency on implementation hashes
# that a reviewer must look at, so it fails instead of being silently re-blessed.
ALLOWED_REBIND_FILES = frozenset({
    "config/release-qualification.json",
    "docs/progress/issue-51-approved-retrieval.json",
    "docs/progress/issue-51-candidate-retrieval-baseline.json",
    "docs/progress/issue-51-completion-retrieval.json",
    "docs/progress/issue-51-final-answer-remediation.json",
    "docs/progress/issue-51-live-passage-checks.json",
    "docs/progress/issue-51-partial-answer-check.json",
    "docs/progress/issue-51-retrieval-remediation.json",
    "docs/progress/release-evaluation-current.json",
})


def copy_repository(destination: str | Path) -> Path:
    """Copy the repository files that evidence verification reads."""

    target = Path(destination)
    shutil.copytree(ROOT, target, ignore=_IGNORED, dirs_exist_ok=True)
    return target


def rebind_implementation_hashes(
    root: str | Path, evidence_files: list[str | Path] | None = None
) -> dict[str, str]:
    """Recompute implementation hashes in the evidence JSON files of `root`.

    Returns {old file sha256: new file sha256} for every evidence file that changed, so
    callers holding artifact hashes in memory can follow. Hashes that other evidence
    files record of a rewritten file are updated the same way until nothing changes.
    """

    base = Path(root)
    if evidence_files is None:
        paths = sorted(
            [*base.glob("docs/progress/*.json"), *base.glob("config/*.json")]
        )
        allowed = ALLOWED_REBIND_FILES
    else:
        paths = [base / Path(item) for item in evidence_files]
        allowed = frozenset(Path(item).as_posix() for item in evidence_files)
    original = {path: _sha(path.read_bytes()) for path in paths}
    renames: dict[str, str] = {}
    for _ in range(10):
        changed: list[str] = []
        for path in paths:
            text = path.read_text(encoding="utf-8")
            replacements = dict(_implementation_replacements(base, path, text))
            replacements.update(renames)
            new_text = text
            for old, new in replacements.items():
                if old != new:
                    new_text = new_text.replace(old, new)
            if new_text != text:
                path.write_bytes(new_text.encode("utf-8"))
                changed.append(path.relative_to(base).as_posix())
        renames = {
            original[path]: _sha(path.read_bytes())
            for path in paths
            if original[path] != _sha(path.read_bytes())
        }
        if not changed:
            break
    else:
        raise AssertionError(
            "evidence rebinding did not converge; still changing: "
            + ", ".join(sorted(changed))
        )
    touched = {
        path.relative_to(base).as_posix()
        for path in paths
        if original[path] != _sha(path.read_bytes())
    }
    unexpected = touched - allowed
    assert not unexpected, f"rebinding touched unexpected evidence files: {sorted(unexpected)}"
    return renames


def _implementation_replacements(base: Path, path: Path, text: str):
    try:
        document = json.loads(text)
    except ValueError:
        return
    for key, value in _walk(document):
        if key == "implementation_sha256" and isinstance(value, dict):
            for relative, recorded in value.items():
                current = _RENAMED_IMPLEMENTATIONS.get(relative, relative)
                source = base / current
                if not (_is_hash(recorded) and source.is_file()):
                    continue
                if current != relative:
                    yield (
                        f'"{relative}": "{recorded}"',
                        f'"{current}": "{_sha(source.read_bytes())}"',
                    )
                else:
                    yield recorded, _sha(source.read_bytes())
        elif key == "retrieval_implementation_sha256" and _is_hash(value):
            yield value, _sha((base / "danish_rag/retrieval.py").read_bytes())
        elif key == "collector_implementation_sha256" and _is_hash(value):
            relative = _COLLECTORS.get(path.name, _DEFAULT_COLLECTOR)
            yield value, _sha((base / relative).read_bytes())


def _walk(node: Any):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX.match(value))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
