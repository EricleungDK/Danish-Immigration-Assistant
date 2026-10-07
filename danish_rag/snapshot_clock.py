"""Snapshot evaluation time for the running local app (#67).

The knowledge release is a demo snapshot that is not kept current. Source freshness
(`source_freshness.assess_source_freshness` without `evaluated_at_utc`) would read the
wall clock, so a release stops citing once its review due dates pass. The app instead
evaluates freshness at the active release's snapshot time (its manifest
`created_at_utc`).

Files bound by release-qualification evidence cannot change, so this module swaps the
module-level `datetime` of `source_freshness` once, for a subclass whose `now()` returns
the snapshot time while a `snapshot_clock` scope is active and otherwise defers to
whatever `datetime` was there before (real, or the #64 test pin). The scope lives in a
`ContextVar`: it applies per request/task or thread, never process-wide, so a second app,
a test, or an evaluation CLI in the same process keeps its own clock.

Only `source_freshness` is wrapped: the answer path, retrieval eligibility and trust
indicators all call it without an evaluation time. Installation and indexing need no
scope: eligibility is judged at retrieval time, not when documents are indexed.
`evidence_integrity` and
`grounded_flexibility_evaluation` supply times only to evidence/qualification code, which
keeps the wall clock (or the test pin).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import danish_rag.source_freshness as source_freshness
from .knowledge_release import load_active_release

CLOCK_MODULES = (source_freshness,)

_REAL_DATETIME = datetime
_MARKER = "_snapshot_clock_wrapper"


class _LazySnapshot:
    """Resolve the snapshot time on first use, once per scope."""

    def __init__(self, resolve: Callable[[], datetime | None]) -> None:
        self._resolve = resolve
        self._resolved = False
        self._value: datetime | None = None

    def get(self) -> datetime | None:
        if not self._resolved:
            try:
                self._value = self._resolve()
            except Exception:
                self._value = None  # fail towards the underlying clock
            self._resolved = True
        return self._value


_ACTIVE: ContextVar[_LazySnapshot | None] = ContextVar("snapshot_clock", default=None)


def _wrap(base: type) -> type:
    meta = type(
        "SnapshotDatetimeMeta",
        (type(base),),
        {
            # isinstance(real_datetime, wrapper) stays True, as in production.
            "__instancecheck__": lambda cls, instance: isinstance(instance, _REAL_DATETIME),
        },
    )

    class SnapshotDatetime(base, metaclass=meta):  # type: ignore[valid-type, misc]
        @classmethod
        def now(cls, tz=None):  # mirrors datetime.now
            scope = _ACTIVE.get()
            snapshot = scope.get() if scope is not None else None
            if snapshot is None:
                return base.now(tz)
            return snapshot.astimezone(tz) if tz else snapshot.replace(tzinfo=None)

    setattr(SnapshotDatetime, _MARKER, True)
    return SnapshotDatetime


def install_snapshot_clock() -> None:
    """Idempotently wrap the current `datetime` of each clock module."""

    for module in CLOCK_MODULES:
        if not getattr(module.datetime, _MARKER, False):
            module.datetime = _wrap(module.datetime)


@contextmanager
def snapshot_clock(resolve: Callable[[], datetime | None]) -> Iterator[None]:
    """Evaluate freshness at `resolve()` inside this scope (lazily, once).

    `resolve` returning None or raising leaves the underlying clock in effect.
    """

    install_snapshot_clock()
    token = _ACTIVE.set(_LazySnapshot(resolve))
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def parse_snapshot_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Release created_at_utc must be a string.")
    parsed = _REAL_DATETIME.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Release created_at_utc must include a UTC offset.")
    return parsed.astimezone(timezone.utc)


def active_snapshot_time(
    data_dir: str | Path,
    *,
    trust_root_path: str | Path | None = None,
) -> datetime | None:
    """Snapshot time of the verified active release, or None if it cannot be read.

    Resolved per request, so install and rollback take effect on the next request without
    any stored state. No active release (or an unreadable one) yields None, leaving the
    underlying clock in effect; the request then fails or installs through the normal path.
    """

    try:
        active = load_active_release(data_dir, trust_root_path=trust_root_path)
    except Exception:
        return None
    return parse_snapshot_time(active["manifest"]["created_at_utc"])
