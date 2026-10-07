"""Pinned freshness clock for tests and the browser fixture server (#64).

Fixture releases carry real review due dates, so evaluating freshness against the
wall clock makes the suites expire. The production module is not changed (its hash
is bound by release evidence); its module-level `datetime` is swapped instead.
"""

from __future__ import annotations

from datetime import datetime, timezone

import danish_rag.source_freshness as source_freshness

# Before the bundled fixture release's review due dates (2026-10-06T12:00:00Z).
FIXTURE_EVALUATION_TIME_UTC = datetime(2026, 10, 1, tzinfo=timezone.utc)


class _PinnedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D401 - mirrors datetime.now
        pinned = FIXTURE_EVALUATION_TIME_UTC
        return pinned.astimezone(tz) if tz else pinned.replace(tzinfo=None)


def pin_freshness_clock() -> None:
    source_freshness.datetime = _PinnedDatetime


def unpin_freshness_clock() -> None:
    source_freshness.datetime = datetime
