"""Shared canonicalization primitives for release and evaluation evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def reject_duplicate_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting ambiguous duplicate field names."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def canonical_json_sha256(payload: Any) -> str:
    """Hash the project's stable UTF-8 canonical JSON representation."""

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Hash the exact bytes of a required evidence file."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc_now_seconds() -> str:
    """Return an RFC 3339 UTC timestamp with stable second precision."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def is_utc_seconds(value: Any) -> bool:
    """Return whether a value is RFC 3339 UTC at exact second precision."""

    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == value
