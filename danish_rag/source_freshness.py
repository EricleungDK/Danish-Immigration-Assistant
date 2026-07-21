"""Fresh Tomato Score inputs and dynamic source eligibility."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


ELIGIBLE_REVIEW_STATES = {"approved-current", "overdue-policy-usable"}
ELIGIBLE_SOURCE_HEALTH = {"healthy", "overdue-policy-usable"}


@dataclass(frozen=True)
class SourceFreshnessAssessment:
    level: str
    reason: str
    answer_eligible: bool


def assess_source_freshness(
    evidence: dict[str, Any],
    *,
    evaluated_at_utc: str | datetime | None = None,
) -> SourceFreshnessAssessment:
    """Calculate source freshness without changing evidence-confidence semantics."""

    base_eligible = (
        evidence.get("review_state") in ELIGIBLE_REVIEW_STATES
        and evidence.get("source_health") in ELIGIBLE_SOURCE_HEALTH
        and evidence.get("approval_state", "approved") == "approved"
    )
    if not base_eligible:
        return SourceFreshnessAssessment(
            level="Low",
            reason=(
                "Source freshness is low: the material source is not in an approved, "
                "answer-eligible state."
            ),
            answer_eligible=False,
        )

    inputs = evidence.get("fresh_tomato_inputs")
    if not isinstance(inputs, dict) or not inputs:
        if evidence.get("source_health") == "overdue-policy-usable":
            return SourceFreshnessAssessment(
                level="Medium",
                reason=(
                    "Source freshness is medium: the material source is policy-usable "
                    "but overdue for review."
                ),
                answer_eligible=True,
            )
        return SourceFreshnessAssessment(
            level="High",
            reason="Source freshness is high: the material source is current and healthy.",
            answer_eligible=True,
        )

    now = _parse_utc(evaluated_at_utc) if evaluated_at_utc else datetime.now(timezone.utc)
    blocked_after = inputs.get("overdue_blocked_after_utc")
    if blocked_after:
        parsed_blocked_after = _try_parse_utc(blocked_after)
        if parsed_blocked_after is None:
            return _invalid_metadata_assessment()
        if now > parsed_blocked_after:
            return SourceFreshnessAssessment(
                level="Low",
                reason=(
                    "Source freshness is low: the source passed its policy block-after "
                    "date and is blocked from supporting answers."
                ),
                answer_eligible=False,
            )

    if evidence.get("source_health") == "overdue-policy-usable":
        return SourceFreshnessAssessment(
            level="Medium",
            reason=(
                "Source freshness is medium: the material source is explicitly marked "
                "policy-usable but overdue for review."
            ),
            answer_eligible=True,
        )

    due = inputs.get("next_review_due_utc")
    if due:
        parsed_due = _try_parse_utc(due)
        if parsed_due is None:
            return _invalid_metadata_assessment()
        if now > parsed_due:
            policy_allows_overdue_use = (
                evidence.get("review_state") == "overdue-policy-usable"
                or bool(blocked_after)
            )
            if not policy_allows_overdue_use:
                return SourceFreshnessAssessment(
                    level="Low",
                    reason=(
                        "Source freshness is low: its scheduled review is overdue and "
                        "the release records no policy allowance for continued use."
                    ),
                    answer_eligible=False,
                )
            return SourceFreshnessAssessment(
                level="Medium",
                reason=(
                    "Source freshness is medium: the source review is overdue but the "
                    "policy block-after date has not been reached."
                ),
                answer_eligible=True,
            )

    declared_health = str(inputs.get("source_health", "current")).casefold()
    if declared_health not in {"current", "healthy"}:
        return SourceFreshnessAssessment(
            level="Low",
            reason=(
                "Source freshness is low: release metadata does not mark the source "
                "content as current and healthy."
            ),
            answer_eligible=False,
        )
    return SourceFreshnessAssessment(
        level="High",
        reason=(
            "Source freshness is high: the material source is current, healthy, and "
            "within its scheduled review period."
        ),
        answer_eligible=True,
    )


def _invalid_metadata_assessment() -> SourceFreshnessAssessment:
    return SourceFreshnessAssessment(
        level="Low",
        reason=(
            "Source freshness is low: release freshness metadata is invalid, so the "
            "source is blocked from supporting answers."
        ),
        answer_eligible=False,
    )


def _try_parse_utc(value: Any) -> datetime | None:
    try:
        return _parse_utc(value)
    except (TypeError, ValueError):
        return None


def _parse_utc(value: str | datetime | Any) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None:
        raise ValueError("freshness timestamps must include a UTC offset")
    return parsed.astimezone(timezone.utc)
