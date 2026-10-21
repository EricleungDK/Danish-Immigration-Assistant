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
_DEFAULT_COLLECTOR = "danish_rag/grounded_flexibility_retrieval.py"


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
    else:
        paths = [base / Path(item) for item in evidence_files]
    original = {path: _sha(path.read_bytes()) for path in paths}
    renames: dict[str, str] = {}
    for _ in range(10):
        changed = False
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
                changed = True
        renames = {
            original[path]: _sha(path.read_bytes())
            for path in paths
            if original[path] != _sha(path.read_bytes())
        }
        if not changed:
            break
    return renames


def _implementation_replacements(base: Path, path: Path, text: str):
    try:
        document = json.loads(text)
    except ValueError:
        return
    for key, value in _walk(document):
        if key == "implementation_sha256" and isinstance(value, dict):
            for relative, recorded in value.items():
                source = base / relative
                if _is_hash(recorded) and source.is_file():
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
