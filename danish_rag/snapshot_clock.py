"""Snapshot evaluation time for the running local app (#67).

The knowledge releases bundled with this repository are demo snapshots that are not kept
current. Source freshness (`source_freshness.assess_source_freshness` without
`evaluated_at_utc`) would read the wall clock, so a bundled release stops citing once its
review due dates pass. The running app instead evaluates freshness at the bundled
release's snapshot time (its manifest `created_at_utc`).

Scope. Rebasing applies ONLY to a release whose full signed manifest is identical to the
manifest of a release shipped in `data/knowledge_releases/` (matched by release ID and
manifest content, not by name). Any other release (for example one installed from GitHub
later) keeps wall-clock freshness, so its real-time due and block-after dates still
fire, and no snapshot label is shown for it.

Mechanism. Files bound by release-qualification evidence cannot change, so this module
swaps the module-level `datetime` of `source_freshness` once, for a subclass whose
`now()` returns the snapshot time while a `snapshot_clock` scope is active and otherwise
defers to whatever `datetime` was there before (real, or the #64 test pin). The scope
holds a per-request `SnapshotState` in a `ContextVar`: it applies per request/task or
thread, never process-wide. The same state object supplies the clock, the UI label and
the stored per-turn metadata, so they cannot disagree. `now()` only reads the state; the
active release is read once per request (`SnapshotState.resolve_active`), never inside
`now()`.

Only `source_freshness` is wrapped: the answer path, retrieval eligibility and trust
indicators all call it without an evaluation time. `evidence_integrity` and
`grounded_flexibility_evaluation` supply times only to evidence/qualification code, which
keeps the wall clock (or the test pin). Installation and indexing need no scope:
eligibility is judged at retrieval time, not when documents are indexed.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import danish_rag.source_freshness as source_freshness
from .knowledge_release import (
    ACTIVE_RELEASE_FILE,
    ACTIVE_RELEASE_TRANSACTION_FILE,
    DEFAULT_RELEASE_CATALOG_DIR,
    load_active_release,
)

LOGGER = logging.getLogger(__name__)

CLOCK_MODULES = (source_freshness,)
# Releases shipped in this repository; tests may point this elsewhere.
BUNDLED_CATALOG_DIR = DEFAULT_RELEASE_CATALOG_DIR

_REAL_DATETIME = datetime
_MARKER = "_snapshot_clock_wrapper"
_RELEASE_ID = re.compile(r"kr-\d{4}-\d{2}-\d{2}\.\d+")


@dataclass(frozen=True)
class Snapshot:
    release_id: str
    time: datetime

    @property
    def date(self) -> str:
        return self.time.date().isoformat()

    def metadata(self) -> dict[str, Any]:
        return {"release_id": self.release_id, "snapshot_date": self.date, "kept_current": False}


@dataclass
class SnapshotState:
    """Per-request snapshot resolution shared by the clock, the label and stored turns."""

    snapshot: Snapshot | None = None
    # True when the active release could not be read: freshness then uses the wall clock
    # and the UI must not claim a snapshot basis.
    unavailable: bool = False

    @property
    def time(self) -> datetime | None:
        return self.snapshot.time if self.snapshot else None

    def metadata(self) -> dict[str, Any] | None:
        return self.snapshot.metadata() if self.snapshot else None

    def adopt(self, manifest: dict[str, Any]) -> None:
        """Take the snapshot (if any) of a manifest the request actually loaded."""

        self.snapshot = bundled_snapshot(manifest)
        self.unavailable = False

    def resolve_active(
        self, data_dir: str | Path, *, trust_root_path: str | Path | None = None
    ) -> None:
        """Resolve from one signature-verified read of the active release."""

        try:
            active = load_active_release(data_dir, trust_root_path=trust_root_path)
        except FileNotFoundError:
            self.snapshot, self.unavailable = None, False  # nothing installed yet
            return
        except Exception as exc:
            LOGGER.warning(
                "Active release unreadable; freshness uses the wall clock and no "
                "snapshot is claimed: %s",
                exc,
            )
            self.snapshot, self.unavailable = None, True
            return
        self.adopt(active["manifest"])


class SnapshotResolver:
    """Resolve each request's state from the active release, verifying only on change.

    A successful, signature-verified resolution is reused while `active-release.json` is
    unchanged (same mtime, size and inode, and no activation transaction pending);
    install and rollback replace that file, so the next request reads again. Failures are
    never cached, so an unreadable release is retried and logged on every request.
    """

    def __init__(self, data_dir: str | Path, *, trust_root_path: str | Path | None = None):
        self.data_dir = Path(data_dir)
        self.trust_root_path = trust_root_path
        # (pointer key, snapshot) replaced as one tuple: safe across worker threads.
        self._cached: tuple[tuple[int, int, int], Snapshot | None] | None = None

    def _pointer_key(self) -> tuple[int, int, int] | None:
        try:
            if (self.data_dir / ACTIVE_RELEASE_TRANSACTION_FILE).exists():
                return None
            stat = (self.data_dir / ACTIVE_RELEASE_FILE).stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size, stat.st_ino)

    def __call__(self, state: SnapshotState) -> None:
        key = self._pointer_key()
        cached = self._cached
        if key is not None and cached is not None and cached[0] == key:
            state.snapshot, state.unavailable = cached[1], False
            return
        state.resolve_active(self.data_dir, trust_root_path=self.trust_root_path)
        # Cache only if the pointer did not change while it was being read.
        if not state.unavailable and key is not None and key == self._pointer_key():
            self._cached = (key, state.snapshot)
        else:
            self._cached = None


_STATE: ContextVar[SnapshotState | None] = ContextVar("snapshot_state", default=None)


def current_snapshot_state() -> SnapshotState:
    """The request's state, or an empty one outside a request."""

    return _STATE.get() or SnapshotState()


def parse_snapshot_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Release created_at_utc must be a string.")
    parsed = _REAL_DATETIME.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Release created_at_utc must include a UTC offset.")
    return parsed.astimezone(timezone.utc)


def bundled_snapshot(manifest: dict[str, Any]) -> Snapshot | None:
    """Snapshot of a manifest identical to a bundled release's, else None.

    The manifest comes from a signature-verified active release; equality with the
    repository's shipped manifest (integrity hashes and signature reference included)
    ties it to the bundled release, not merely to its name.
    """

    try:
        release_id = manifest["knowledge_release_id"]
        if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
            return None
        bundled = json.loads(
            (Path(BUNDLED_CATALOG_DIR) / release_id / "manifest.json").read_text(encoding="utf-8")
        )
        if bundled != manifest:
            return None
        return Snapshot(release_id, parse_snapshot_time(manifest["created_at_utc"]))
    except (KeyError, TypeError, ValueError, OSError):
        return None


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
            state = _STATE.get()
            snapshot_time = state.time if state is not None else None
            if snapshot_time is None:
                return base.now(tz)
            if tz is not None:
                return snapshot_time.astimezone(tz)
            return snapshot_time.astimezone().replace(tzinfo=None)  # naive local time

    setattr(SnapshotDatetime, _MARKER, True)
    return SnapshotDatetime


def install_snapshot_clock() -> None:
    """Idempotently wrap the current `datetime` of each clock module."""

    for module in CLOCK_MODULES:
        # vars(): a subclass of the wrapper inherits the marker and must still be wrapped.
        if _MARKER not in vars(module.datetime):
            module.datetime = _wrap(module.datetime)


@contextmanager
def snapshot_clock(state: SnapshotState) -> Iterator[None]:
    """Evaluate freshness at `state.time` inside this scope (the wall clock if unset)."""

    install_snapshot_clock()
    token = _STATE.set(state)
    try:
        yield
    finally:
        _STATE.reset(token)
