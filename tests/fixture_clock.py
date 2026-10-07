"""Pinned freshness clock for tests and the browser fixture server (#64).

Fixture releases carry real review due dates, so evaluating freshness against the
wall clock makes the suites expire. Production modules are not changed (their hashes
are bound by release evidence); the module-level `datetime` of every module that
supplies a freshness evaluation time is swapped instead:

- `source_freshness`: callers that pass no `evaluated_at_utc`
- `evidence_integrity`: `utc_now_seconds()`, passed as `evaluated_at_utc` by the
  retrieval/release evidence paths
- `grounded_flexibility_evaluation`: its own `_utc_now()`
"""

from __future__ import annotations

from datetime import datetime, timezone

import danish_rag.evidence_integrity as evidence_integrity
import danish_rag.grounded_flexibility_evaluation as grounded_flexibility_evaluation
import danish_rag.source_freshness as source_freshness

# Before the fixture releases' review due dates (bundled minimal release:
# 2026-10-06T12:00:00Z); a test asserts this against the manifests.
FIXTURE_EVALUATION_TIME_UTC = datetime(2026, 10, 1, tzinfo=timezone.utc)

PINNED_MODULES = (source_freshness, evidence_integrity, grounded_flexibility_evaluation)


class _RealDatetimeInstances(type):
    # isinstance(real_datetime, _PinnedDatetime) stays True, as in production.
    def __instancecheck__(cls, instance: object) -> bool:
        return isinstance(instance, datetime)


class _PinnedDatetime(datetime, metaclass=_RealDatetimeInstances):
    @classmethod
    def now(cls, tz=None):  # mirrors datetime.now
        pinned = FIXTURE_EVALUATION_TIME_UTC
        return pinned.astimezone(tz) if tz else pinned.replace(tzinfo=None)


def pin_freshness_clock() -> None:
    for module in PINNED_MODULES:
        module.datetime = _PinnedDatetime


def unpin_freshness_clock() -> None:
    for module in PINNED_MODULES:
        module.datetime = datetime
