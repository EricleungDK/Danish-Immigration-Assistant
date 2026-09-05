"""Pin synthetic corpus tests to their recorded review period."""

from datetime import datetime, timezone
from unittest.mock import patch


class FixtureReviewTime(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = cls(2026, 7, 30, 16, tzinfo=timezone.utc)
        return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)


def fixture_review_time():
    return patch("danish_rag.source_freshness.datetime", FixtureReviewTime)
