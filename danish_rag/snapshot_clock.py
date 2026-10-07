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
thread, never process-wide.

The state is derived ONLY from the signature-verified release the request actually uses
(`SnapshotState.adopt(manifest)`): the result of the handler's `ensure_release()` and, in
the answer path, the retriever's manifest. The data judged is the data loaded, so the
clock, the UI label and the stored turn agree. This module does no release I/O and adds
no verification. Until a release has been verified in the request the state is "none"
(wall clock); if loading fails it becomes "unavailable" (wall clock, warning logged, UI
says so). There is no path that rebases freshness without a verified release in the same
request.

Only `source_freshness` is wrapped: the answer path, retrieval eligibility and trust
indicators all call it without an evaluation time. `evidence_integrity` and
`grounded_flexibility_evaluation` supply times only to evidence/qualification code, which
keeps the wall clock (or the test pin). Installation and indexing need no scope:
eligibility is judged at retrieval time, not when documents are indexed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import danish_rag.source_freshness as source_freshness
from .knowledge_release import DEFAULT_RELEASE_CATALOG_DIR

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
    """Per-request snapshot basis shared by the clock, the label and stored turns."""

    snapshot: Snapshot | None = None
    # True when the request's release could not be loaded: freshness then uses the wall
    # clock and the UI must not claim a snapshot basis.
    unavailable: bool = False
    # Once the basis a request's answer was judged on is settled, later loads in the same
    # request (for example the page render) cannot replace it.
    frozen: bool = False
    # Set when, after freezing, the page render loaded a different release or failed:
    # the page then says the active release changed after the answer was prepared.
    changed_after_freeze: bool = False
    # Set when, after freezing, re-loading the release for the page render failed.
    reload_failed_after_freeze: bool = False
    _adopted: dict[str, Any] | None = field(default=None, repr=False, compare=False)
    _adopted_digest: str | None = field(default=None, repr=False, compare=False)

    @property
    def time(self) -> datetime | None:
        return self.snapshot.time if self.snapshot else None

    def metadata(self) -> dict[str, Any] | None:
        return self.snapshot.metadata() if self.snapshot else None

    @property
    def status(self) -> str:
        """"snapshot", "unavailable" (load failed, wall clock) or "none" (not snapshot mode)."""

        if self.snapshot:
            return "snapshot"
        return "unavailable" if self.unavailable else "none"

    def adopt(self, manifest: dict[str, Any]) -> None:
        """Take the snapshot (if any) of the verified manifest this request loaded.

        Ignored once frozen. A load failure earlier in the request is sticky: the request
        stays on the wall clock with no snapshot claim even if a later load succeeds. A
        manifest equal to the one already adopted (another load of the same verified
        release) is not re-evaluated.
        """

        if self.unavailable:
            return
        if self._is_adopted(manifest):
            return
        if self.frozen:
            self.changed_after_freeze = True
            return
        digest = _safe_digest(manifest)
        self.snapshot = bundled_snapshot(manifest, digest=digest)
        self._adopted = manifest
        self._adopted_digest = digest

    def _is_adopted(self, manifest: dict[str, Any]) -> bool:
        # Canonical digest, not ==: 1, 1.0 and true must not count as the same manifest.
        if self._adopted is None:
            return False
        if manifest is self._adopted:
            return True
        digest = _safe_digest(manifest)
        if digest is None or self._adopted_digest is None:
            # Not canonically digestable (e.g. NaN): fall back to plain equality, which
            # can only report "same" for the same values, never invent a change.
            return digest is None and self._adopted_digest is None and manifest == self._adopted
        return digest == self._adopted_digest

    def mark_unavailable(self, reason: BaseException) -> None:
        """The request's release could not be loaded: wall clock, no snapshot claim."""

        if self.frozen:
            # The answer's basis is settled; the page reports the error separately.
            self.reload_failed_after_freeze = True
            return
        LOGGER.warning(
            "Release could not be loaded; freshness uses the wall clock and no snapshot "
            "is claimed: %s",
            reason,
        )
        self.snapshot, self.unavailable = None, True

    def freeze(self) -> None:
        self.frozen = True


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


def _manifest_digest(manifest: dict[str, Any]) -> str:
    """sha256 of the canonical JSON: unlike ==, 1, 1.0 and true differ and NaN is refused."""

    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_digest(manifest: dict[str, Any]) -> str | None:
    try:
        return _manifest_digest(manifest)
    except (TypeError, ValueError):
        return None


# manifest path -> (sha256 of the file bytes, canonical digest). The small file is read
# and hashed on every call so a same-size, mtime-preserving replacement is still seen;
# only the JSON parse and canonicalisation are memoized.
_BUNDLED_DIGESTS: dict[str, tuple[str, str]] = {}


def _bundled_digest(release_id: str) -> str:
    path = Path(BUNDLED_CATALOG_DIR) / release_id / "manifest.json"
    raw = path.read_bytes()
    content = hashlib.sha256(raw).hexdigest()
    cached = _BUNDLED_DIGESTS.get(str(path))
    if cached is not None and cached[0] == content:
        return cached[1]
    digest = _manifest_digest(json.loads(raw))
    _BUNDLED_DIGESTS[str(path)] = (content, digest)
    return digest


def bundled_snapshot(manifest: dict[str, Any], *, digest: str | None = None) -> Snapshot | None:
    """Snapshot of a manifest identical to a bundled release's, else None.

    The manifest comes from a signature-verified release; identity of its canonical JSON
    digest with the repository's shipped manifest (integrity hashes and signature
    reference included) ties it to the bundled release, not merely to its name.
    """

    try:
        release_id = manifest["knowledge_release_id"]
        if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
            return None
        if (digest or _manifest_digest(manifest)) != _bundled_digest(release_id):
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
