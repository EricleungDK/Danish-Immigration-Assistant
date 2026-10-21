"""Review period of the synthetic corpora in the exam-coverage, multi-intent and grounded
flexibility suites: they pass this time through the entry points (#70)."""

from datetime import datetime, timezone

REVIEW_TIME = datetime(2026, 7, 30, 16, tzinfo=timezone.utc)


def review_clock() -> datetime:
    return REVIEW_TIME
