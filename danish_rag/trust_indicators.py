"""Trust indicators: material-source health, eligibility, Evidence confidence, Fresh Tomato Score.

Everything here is a pure function of material-source evidence plus an explicit,
timezone-aware evaluation time. There is no wall-clock fallback: entry points own the
clock (#70). The answer-level Fresh Tomato Score is the lowest score among material
sources.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, StrEnum
from typing import Any


class SourceHealth(StrEnum):
    """Material-source health as stored in releases and Conversation records."""

    HEALTHY = "healthy"
    OVERDUE_POLICY_USABLE = "overdue-policy-usable"
    CHANGED_UNREVIEWED = "changed-unreviewed"
    BROKEN = "broken"
    EXTRACTION_FAILED = "extraction-failed"


class TrustLevel(Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


ELIGIBLE_REVIEW_STATES = {"approved-current", "overdue-policy-usable"}
ELIGIBLE_SOURCE_HEALTH = frozenset({SourceHealth.HEALTHY, SourceHealth.OVERDUE_POLICY_USABLE})
CONFLICTING_AGREEMENT_STATES = {"conflict", "conflicts", "conflicting", "contradicts"}
_TRUST_ORDER = {TrustLevel.LOW: 0, TrustLevel.MEDIUM: 1, TrustLevel.HIGH: 2}


@dataclass(frozen=True)
class SourceFreshnessAssessment:
    level: str
    reason: str
    answer_eligible: bool


def assess_source_freshness(
    evidence: dict[str, Any],
    *,
    evaluated_at_utc: str | datetime,
) -> SourceFreshnessAssessment:
    """Calculate source freshness at an explicit, timezone-aware evaluation time.

    There is no wall-clock fallback: entry points own the clock (#70).
    """

    now = _parse_utc(evaluated_at_utc)
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
        if is_overdue_policy_usable(evidence):
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

    if is_overdue_policy_usable(evidence):
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


def is_answer_eligible(evidence: dict[str, Any], *, evaluated_at_utc: str | datetime) -> bool:
    """Whether a material source may support answers at the evaluation time."""

    return assess_source_freshness(evidence, evaluated_at_utc=evaluated_at_utc).answer_eligible


def is_overdue_policy_usable(evidence: dict[str, Any]) -> bool:
    return evidence.get("source_health") == SourceHealth.OVERDUE_POLICY_USABLE


def is_conflicting_evidence(evidence: dict[str, Any]) -> bool:
    if evidence.get("conflicts_with_answer") is True:
        return True
    agreement_state = str(evidence.get("agreement_state", "supports")).casefold()
    return agreement_state in CONFLICTING_AGREEMENT_STATES


def lowest_trust_level(levels: list[TrustLevel]) -> TrustLevel:
    """Lowest material source wins; no material source is Low."""

    if not levels:
        return TrustLevel.LOW
    return min(levels, key=lambda level: _TRUST_ORDER.get(level, 0))


def fresh_tomato_for_source(
    evidence: dict[str, Any], *, evaluated_at: datetime
) -> tuple[TrustLevel, str]:
    assessment = assess_source_freshness(evidence, evaluated_at_utc=evaluated_at)
    return TrustLevel(assessment.level), assessment.reason


def assess_trust_indicators(
    *,
    official_fact_count: int,
    cited_official_fact_count: int,
    used_evidence: list[dict[str, Any]],
    evaluated_at: datetime,
) -> dict[str, str]:
    """Evidence confidence and Fresh Tomato Score for an answer's material sources."""

    source_scores = [
        fresh_tomato_for_source(item, evaluated_at=evaluated_at)[0] for item in used_evidence
    ]
    fresh_tomato_score = lowest_trust_level(source_scores)
    conflicting_sources = [
        str(item["citation_id"]) for item in used_evidence if is_conflicting_evidence(item)
    ]
    coverage_complete = (
        bool(official_fact_count) and cited_official_fact_count == official_fact_count
    )
    if official_fact_count == 0:
        evidence_confidence = TrustLevel.LOW
        evidence_reason = (
            "No official fact was produced; the response is limited to an "
            "evidence-bounded refusal or source warning."
        )
    elif coverage_complete and not conflicting_sources:
        evidence_confidence = TrustLevel.HIGH
        evidence_reason = (
            f"Evidence coverage is complete for {official_fact_count} official fact(s); "
            "cited material sources are attached directly to the claims and no retrieved "
            "material source is marked as conflicting."
        )
    elif conflicting_sources:
        evidence_confidence = TrustLevel.LOW
        evidence_reason = (
            "Evidence agreement is incomplete: conflicting material source(s) were retrieved "
            f"for this answer ({', '.join(sorted(conflicting_sources))})."
        )
    else:
        evidence_confidence = TrustLevel.LOW
        evidence_reason = (
            f"Evidence coverage is incomplete: {cited_official_fact_count} of "
            f"{official_fact_count} official fact(s) have adjacent approved-source citations."
        )
    return {
        "evidence_confidence": evidence_confidence.value,
        "evidence_confidence_reason": evidence_reason,
        "fresh_tomato_score": fresh_tomato_score.value,
        "fresh_tomato_reason": _fresh_tomato_reason(fresh_tomato_score),
    }


def no_material_source_trust_indicators() -> dict[str, str]:
    """Trust indicators of a refusal that no eligible material source could support."""

    return {
        "evidence_confidence": TrustLevel.LOW.value,
        "evidence_confidence_reason": (
            "No eligible approved official material source could support a "
            "substantive answer."
        ),
        "fresh_tomato_score": TrustLevel.LOW.value,
        "fresh_tomato_reason": _fresh_tomato_reason(TrustLevel.LOW),
    }


def _fresh_tomato_reason(score: TrustLevel) -> str:
    if score is TrustLevel.HIGH:
        return "Source freshness is high because all material sources are current and healthy."
    if score is TrustLevel.MEDIUM:
        return (
            "Source freshness is medium because the lowest material-source freshness score "
            "is Medium."
        )
    return "Source freshness is low because no current healthy material source could be attached."
