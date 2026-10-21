"""Fixed evaluation time for suites (#70).

Fixture knowledge releases carry real review due dates, so tests pass this time through
the entry points (`clock=`) instead of reading the wall clock. It precedes every bundled
release's review due date (bundled minimal release: 2026-10-06T12:00:00Z).
"""

from __future__ import annotations

from datetime import datetime, timezone

FIXED_TIME = datetime(2026, 10, 1, tzinfo=timezone.utc)
FIXED_TIME_ISO = "2026-10-01T00:00:00Z"


def fixed_clock() -> datetime:
    return FIXED_TIME
