"""Machine-readable source-registry validation and production qualification."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


SOURCE_REGISTRY_SCHEMA_VERSION = "1.0"

_SOURCE_STATES = {
    "discovered",
    "candidate-approved-url",
    "fetch-failed",
    "broken",
    "redirected-pending-review",
    "extraction-failed",
    "changed-unreviewed",
    "approved-current",
    "overdue-policy-usable",
    "overdue-blocked",
    "withdrawn",
    "superseded",
}
_PRODUCTION_ELIGIBLE_STATES = {"approved-current", "overdue-policy-usable"}
_CONTENT_ORIGINS = {
    "official-source-normalized-extract",
    "project-authored-fixture",
}
_PLACEHOLDER_IDENTITY_MARKERS = {"fixture", "placeholder", "example", "test-only"}
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class SourceRegistryError(ValueError):
    """Raised when source-registry evidence is incomplete or inconsistent."""


def load_source_registry(path: str | Path) -> dict[str, Any]:
    """Load and validate one machine-readable source-registry artifact."""

    resolved_path = Path(path)
    try:
        registry = json.loads(resolved_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SourceRegistryError(f"Could not read source registry: {resolved_path}") from exc
    except json.JSONDecodeError as exc:
        raise SourceRegistryError(f"Source registry is not valid JSON: {resolved_path}") from exc
    if not isinstance(registry, dict):
        raise SourceRegistryError("Source registry must be a JSON object")
    _validate_registry(registry)
    _validate_declared_qualification(registry)
    return registry


def assess_source_registry_qualification(registry: dict[str, Any]) -> dict[str, Any]:
    """Derive whether registry evidence can qualify a production knowledge release."""

    _validate_registry(registry)
    result = _assess_validated_registry(registry)
    _validate_declared_qualification(registry, expected=result)
    return result


def validate_source_registry_against_release(
    registry: dict[str, Any],
    release_dir: str | Path,
) -> dict[str, Any]:
    """Cross-check registry evidence against a knowledge-release manifest and corpus."""

    _validate_registry(registry)
    _validate_declared_qualification(registry)
    resolved_release_dir = Path(release_dir)
    manifest_path = resolved_release_dir / "manifest.json"
    documents_path = resolved_release_dir / "corpus" / "documents.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        documents = json.loads(documents_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SourceRegistryError(
            "Could not read knowledge release for registry validation"
        ) from exc
    except json.JSONDecodeError as exc:
        raise SourceRegistryError("Knowledge release contains invalid JSON") from exc
    if not isinstance(manifest, dict) or not isinstance(documents, list):
        raise SourceRegistryError("Knowledge release manifest or documents have invalid shape")

    registry_version = str(registry["source_registry_version"])
    release_id = str(registry["knowledge_release_id"])
    if manifest.get("source_registry_version") != registry_version:
        raise SourceRegistryError("Knowledge release source-registry version does not match")
    if manifest.get("knowledge_release_id") != release_id:
        raise SourceRegistryError("Knowledge release ID does not match source registry")

    registry_by_id = {str(source["source_id"]): source for source in registry["sources"]}
    manifest_by_id = {
        str(source.get("source_id")): source for source in manifest.get("sources", [])
    }
    if set(registry_by_id) != set(manifest_by_id):
        raise SourceRegistryError("Source IDs differ between registry and knowledge release")

    documents_by_source: dict[str, list[dict[str, Any]]] = {
        source_id: [] for source_id in registry_by_id
    }
    for document in documents:
        if not isinstance(document, dict):
            raise SourceRegistryError("Knowledge release document must be an object")
        source_id = str(document.get("source_id", ""))
        if source_id not in documents_by_source:
            raise SourceRegistryError(
                f"Knowledge release document references unknown source {source_id!r}"
            )
        documents_by_source[source_id].append(document)

    fixture_document_count = 0
    for source_id, source in registry_by_id.items():
        manifest_source = manifest_by_id[source_id]
        for field in ("publisher", "official_url", "topic", "language"):
            if source[field] != manifest_source.get(field):
                raise SourceRegistryError(
                    f"Source {source_id} {field} differs between registry and release"
                )
        source_documents = documents_by_source[source_id]
        if not source_documents:
            raise SourceRegistryError(f"Source {source_id} has no corpus document")
        for document in source_documents:
            if document.get("content_origin") != source["content_origin"]:
                raise SourceRegistryError(
                    f"Source {source_id} content origin differs from its corpus document"
                )

        if source["content_origin"] == "project-authored-fixture":
            fixture_document_count += len(source_documents)
            _validate_fixture_projection(
                source,
                manifest_source=manifest_source,
                documents=source_documents,
                release_id=release_id,
            )

    result = _assess_validated_registry(registry)
    return {
        **result,
        "knowledge_release_id": release_id,
        "source_count": len(registry_by_id),
        "fixture_document_count": fixture_document_count,
    }


def _validate_registry(registry: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "source_registry_version",
        "knowledge_release_id",
        "artifact_scope",
        "production_qualification",
        "sources",
    }
    missing = sorted(required - set(registry))
    if missing:
        raise SourceRegistryError(f"Source registry missing field(s): {', '.join(missing)}")
    if registry["schema_version"] != SOURCE_REGISTRY_SCHEMA_VERSION:
        raise SourceRegistryError("Unsupported source-registry schema version")
    if not re.fullmatch(r"sr-\d{4}-\d{2}-\d{2}\.\d+", str(registry["source_registry_version"])):
        raise SourceRegistryError("Invalid source-registry version")
    if not re.fullmatch(r"kr-\d{4}-\d{2}-\d{2}\.\d+", str(registry["knowledge_release_id"])):
        raise SourceRegistryError("Invalid knowledge-release ID")
    if registry["artifact_scope"] not in {
        "fixture-governance-evidence",
        "production-source-registry",
    }:
        raise SourceRegistryError("Invalid source-registry artifact scope")
    sources = registry["sources"]
    if not isinstance(sources, list) or not sources:
        raise SourceRegistryError("Source registry must contain at least one source")

    seen_source_ids: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise SourceRegistryError(f"Source registry entry {index} must be an object")
        _validate_source(source, index=index)
        source_id = str(source["source_id"])
        if source_id in seen_source_ids:
            raise SourceRegistryError(f"Duplicate source ID {source_id!r}")
        seen_source_ids.add(source_id)


def _validate_source(source: dict[str, Any], *, index: int) -> None:
    required = {
        "source_id",
        "publisher",
        "official_url",
        "topic",
        "language",
        "registry_state",
        "content_origin",
        "production_release_eligible",
        "curation_evidence",
        "monitoring_evidence",
        "review_evidence",
    }
    missing = sorted(required - set(source))
    if missing:
        raise SourceRegistryError(
            f"Source registry entry {index} missing field(s): {', '.join(missing)}"
        )
    source_id = str(source["source_id"])
    if not source_id or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", source_id):
        raise SourceRegistryError(f"Source registry entry {index} has invalid source ID")
    for field in ("publisher", "topic", "language"):
        if not isinstance(source[field], str) or not source[field].strip():
            raise SourceRegistryError(f"Source {source_id} has invalid {field}")
    if not str(source["official_url"]).startswith("https://"):
        raise SourceRegistryError(f"Source {source_id} official URL must use HTTPS")
    if source["registry_state"] not in _SOURCE_STATES:
        raise SourceRegistryError(f"Source {source_id} has invalid registry state")
    if source["content_origin"] not in _CONTENT_ORIGINS:
        raise SourceRegistryError(f"Source {source_id} has invalid content origin")
    if not isinstance(source["production_release_eligible"], bool):
        raise SourceRegistryError(f"Source {source_id} eligibility must be boolean")

    _validate_curation_evidence(source_id, source["curation_evidence"])
    _validate_monitoring_evidence(source_id, source["monitoring_evidence"])

    review = source["review_evidence"]
    if not isinstance(review, dict):
        raise SourceRegistryError(f"Source {source_id} review evidence must be an object")
    review_required = {
        "status",
        "assessment_method",
        "reviewed_at_utc",
        "reviewer_ids",
        "official_source_snapshot_sha256",
        "normalized_extraction_sha256",
        "decision",
        "materiality",
        "notes",
        "interpretation_risks",
        "second_reviewer_ids",
    }
    review_missing = sorted(review_required - set(review))
    if review_missing:
        raise SourceRegistryError(
            f"Source {source_id} review evidence missing field(s): {', '.join(review_missing)}"
        )
    status = review["status"]
    if status not in {"not-recorded", "completed"}:
        raise SourceRegistryError(f"Source {source_id} has invalid review status")
    if not isinstance(review["reviewer_ids"], list):
        raise SourceRegistryError(f"Source {source_id} reviewer IDs must be a list")
    if not isinstance(review["second_reviewer_ids"], list):
        raise SourceRegistryError(f"Source {source_id} second reviewer IDs must be a list")
    if not isinstance(review["interpretation_risks"], list):
        raise SourceRegistryError(f"Source {source_id} interpretation risks must be a list")

    if status == "not-recorded":
        fields_that_must_be_empty = (
            "assessment_method",
            "reviewed_at_utc",
            "official_source_snapshot_sha256",
            "normalized_extraction_sha256",
            "decision",
            "materiality",
            "notes",
        )
        if (
            review["reviewer_ids"]
            or review["second_reviewer_ids"]
            or review["interpretation_risks"]
            or any(review[field] is not None for field in fields_that_must_be_empty)
        ):
            raise SourceRegistryError(
                f"Source {source_id} cannot attach review details when review is not recorded"
            )
    else:
        if review["assessment_method"] != "human-source-and-normalized-extraction-review":
            raise SourceRegistryError(
                f"Source {source_id} completed review requires the human review method"
            )
        _parse_utc(review["reviewed_at_utc"], f"Source {source_id} reviewed_at_utc")
        _validate_non_placeholder_identities(
            review["reviewer_ids"],
            source_id=source_id,
            label="reviewer",
        )
        _validate_sha256(
            review["official_source_snapshot_sha256"],
            f"Source {source_id} official snapshot hash",
        )
        _validate_sha256(
            review["normalized_extraction_sha256"],
            f"Source {source_id} normalized extraction hash",
        )
        if review["decision"] not in _PRODUCTION_ELIGIBLE_STATES:
            raise SourceRegistryError(f"Source {source_id} completed review has invalid decision")
        if review["decision"] != source["registry_state"]:
            raise SourceRegistryError(
                f"Source {source_id} review decision does not match its registry state"
            )
        if review["materiality"] not in {"material", "non-material"}:
            raise SourceRegistryError(f"Source {source_id} review has invalid materiality")
        if not isinstance(review["notes"], str) or not review["notes"].strip():
            raise SourceRegistryError(f"Source {source_id} completed review requires notes")
        if any(
            not isinstance(risk, str) or not risk.strip()
            for risk in review["interpretation_risks"]
        ):
            raise SourceRegistryError(
                f"Source {source_id} interpretation risks must be non-empty strings"
            )
        if review["second_reviewer_ids"]:
            _validate_non_placeholder_identities(
                review["second_reviewer_ids"],
                source_id=source_id,
                label="second reviewer",
            )
        if review["materiality"] == "material":
            distinct_reviewers = {
                *review["reviewer_ids"],
                *review["second_reviewer_ids"],
            }
            if len(distinct_reviewers) < 2:
                raise SourceRegistryError(
                    f"Source {source_id} material change requires second human reviewer evidence"
                )

    if source["production_release_eligible"]:
        if source["curation_evidence"]["status"] != "completed":
            raise SourceRegistryError(
                f"Source {source_id} production eligibility requires completed curation"
            )
        if source["monitoring_evidence"]["status"] != "recorded":
            raise SourceRegistryError(
                f"Source {source_id} production eligibility requires monitoring evidence"
            )
        if status != "completed":
            raise SourceRegistryError(
                f"Source {source_id} production eligibility requires completed human review"
            )
        if source["registry_state"] not in _PRODUCTION_ELIGIBLE_STATES:
            raise SourceRegistryError(
                f"Source {source_id} production eligibility requires an eligible registry state"
            )
        if source["content_origin"] != "official-source-normalized-extract":
            raise SourceRegistryError(
                f"Source {source_id} production eligibility requires official-source content"
            )


def _assess_validated_registry(registry: dict[str, Any]) -> dict[str, Any]:
    sources = registry["sources"]
    reason_codes: list[str] = []
    completed = [
        source for source in sources if source["review_evidence"]["status"] == "completed"
    ]
    if registry["artifact_scope"] == "fixture-governance-evidence":
        reason_codes.append("fixture-governance-evidence-only")
    if any(source["curation_evidence"]["status"] != "completed" for source in sources):
        reason_codes.append("source-curation-not-recorded")
    if any(source["monitoring_evidence"]["status"] != "recorded" for source in sources):
        reason_codes.append("source-monitoring-evidence-not-recorded")
    if any(source["content_origin"] == "project-authored-fixture" for source in sources):
        reason_codes.append("project-authored-fixture-content")
    if len(completed) != len(sources):
        reason_codes.append("production-human-source-review-not-recorded")
    if any(
        source["review_evidence"]["official_source_snapshot_sha256"] is None
        for source in sources
    ):
        reason_codes.append("official-source-snapshots-not-recorded")
    if any(not source["production_release_eligible"] for source in sources) and not reason_codes:
        reason_codes.append("source-registry-state-not-production-eligible")

    reason_codes.sort()
    return {
        "source_registry_version": registry["source_registry_version"],
        "status": "blocked" if reason_codes else "qualified",
        "production_release_eligible": not reason_codes,
        "production_human_reviewed_source_count": len(completed),
        "source_count": len(sources),
        "reason_codes": reason_codes,
    }


def _validate_declared_qualification(
    registry: dict[str, Any],
    *,
    expected: dict[str, Any] | None = None,
) -> None:
    declared = registry.get("production_qualification")
    if not isinstance(declared, dict):
        raise SourceRegistryError("Production qualification declaration must be an object")
    derived = expected or _assess_validated_registry(registry)
    for field in ("status", "production_release_eligible", "reason_codes"):
        if declared.get(field) != derived[field]:
            raise SourceRegistryError(
                f"Declared production qualification {field} does not match registry evidence"
            )


def _validate_curation_evidence(source_id: str, evidence: Any) -> None:
    if not isinstance(evidence, dict):
        raise SourceRegistryError(f"Source {source_id} curation evidence must be an object")
    required = {"status", "curator_ids", "admitted_at_utc", "scope_rationale"}
    missing = sorted(required - set(evidence))
    if missing:
        raise SourceRegistryError(
            f"Source {source_id} curation evidence missing field(s): {', '.join(missing)}"
        )
    if evidence["status"] not in {"not-recorded", "completed"}:
        raise SourceRegistryError(f"Source {source_id} has invalid curation status")
    if not isinstance(evidence["curator_ids"], list):
        raise SourceRegistryError(f"Source {source_id} curator IDs must be a list")
    if evidence["status"] == "not-recorded":
        if evidence["curator_ids"] or any(
            evidence[field] is not None for field in ("admitted_at_utc", "scope_rationale")
        ):
            raise SourceRegistryError(
                f"Source {source_id} cannot attach curation details when curation is not recorded"
            )
        return
    _validate_non_placeholder_identities(
        evidence["curator_ids"],
        source_id=source_id,
        label="curator",
    )
    _parse_utc(evidence["admitted_at_utc"], f"Source {source_id} admitted_at_utc")
    if not isinstance(evidence["scope_rationale"], str) or not evidence[
        "scope_rationale"
    ].strip():
        raise SourceRegistryError(
            f"Source {source_id} completed curation requires a scope rationale"
        )


def _validate_monitoring_evidence(source_id: str, evidence: Any) -> None:
    if not isinstance(evidence, dict):
        raise SourceRegistryError(f"Source {source_id} monitoring evidence must be an object")
    required = {
        "status",
        "owner_ids",
        "last_fetched_at_utc",
        "final_url",
        "http_status",
    }
    missing = sorted(required - set(evidence))
    if missing:
        raise SourceRegistryError(
            f"Source {source_id} monitoring evidence missing field(s): {', '.join(missing)}"
        )
    if evidence["status"] not in {"not-recorded", "recorded"}:
        raise SourceRegistryError(f"Source {source_id} has invalid monitoring status")
    if not isinstance(evidence["owner_ids"], list):
        raise SourceRegistryError(f"Source {source_id} monitor owner IDs must be a list")
    monitoring_fields = ("last_fetched_at_utc", "final_url", "http_status")
    if evidence["status"] == "not-recorded":
        if evidence["owner_ids"] or any(evidence[field] is not None for field in monitoring_fields):
            raise SourceRegistryError(
                f"Source {source_id} cannot attach monitoring details when "
                "monitoring is not recorded"
            )
        return
    _validate_non_placeholder_identities(
        evidence["owner_ids"],
        source_id=source_id,
        label="monitor owner",
    )
    _parse_utc(
        evidence["last_fetched_at_utc"],
        f"Source {source_id} last_fetched_at_utc",
    )
    if not isinstance(evidence["final_url"], str) or not evidence["final_url"].startswith(
        "https://"
    ):
        raise SourceRegistryError(f"Source {source_id} monitored final URL must use HTTPS")
    if not isinstance(evidence["http_status"], int) or not 100 <= evidence["http_status"] <= 599:
        raise SourceRegistryError(f"Source {source_id} monitoring HTTP status is invalid")


def _validate_non_placeholder_identities(
    identities: list[Any],
    *,
    source_id: str,
    label: str,
) -> None:
    if not identities or any(
        not isinstance(identity, str) or not identity.strip() for identity in identities
    ):
        raise SourceRegistryError(f"Source {source_id} completed evidence requires {label} IDs")
    for identity in identities:
        folded = identity.casefold()
        if any(marker in folded for marker in _PLACEHOLDER_IDENTITY_MARKERS):
            raise SourceRegistryError(
                f"Source {source_id} uses a placeholder {label} label as human evidence"
            )


def _validate_fixture_projection(
    source: dict[str, Any],
    *,
    manifest_source: dict[str, Any],
    documents: list[dict[str, Any]],
    release_id: str,
) -> None:
    projection = source.get("fixture_projection")
    if not isinstance(projection, dict):
        raise SourceRegistryError(
            f"Source {source['source_id']} is missing its fixture projection"
        )
    expected = {
        "knowledge_release_id": release_id,
        "manifest_review_state": manifest_source.get("review_state"),
        "manifest_reviewer_labels": manifest_source.get("reviewers"),
        "manifest_reviewed_at_utc": manifest_source.get("reviewed_at_utc"),
        "manifest_last_checked_at_utc": manifest_source.get("last_checked_at_utc"),
        "manifest_final_url": manifest_source.get("final_url"),
        "manifest_extraction_schema_version": manifest_source.get(
            "extraction_schema_version"
        ),
        "manifest_fresh_tomato_inputs": manifest_source.get("fresh_tomato_inputs"),
        "manifest_source_content_sha256": manifest_source.get("source_content_sha256"),
        "manifest_normalized_document_sha256": manifest_source.get(
            "normalized_document_sha256"
        ),
        "document_ids": sorted(str(document.get("document_id")) for document in documents),
    }
    observed = {
        **projection,
        "document_ids": sorted(str(value) for value in projection.get("document_ids", [])),
    }
    for field, value in expected.items():
        if observed.get(field) != value:
            raise SourceRegistryError(
                f"Source {source['source_id']} fixture projection {field} does not match release"
            )

    # The active fixture manifest's two provenance-named hashes cover the project-authored
    # summary text. Proving that relationship prevents them from being mistaken for archived
    # official-page or reviewed extraction hashes.
    if len(documents) != 1:
        raise SourceRegistryError(
            f"Source {source['source_id']} fixture projection must identify one summary document"
        )
    fixture_text_hash = hashlib.sha256(
        str(documents[0].get("content", "")).encode("utf-8")
    ).hexdigest()
    if fixture_text_hash != projection["manifest_source_content_sha256"]:
        raise SourceRegistryError(
            f"Source {source['source_id']} fixture projection does not describe fixture text hashes"
        )
    if fixture_text_hash != projection["manifest_normalized_document_sha256"]:
        raise SourceRegistryError(
            f"Source {source['source_id']} fixture projection does not describe fixture text hashes"
        )


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SourceRegistryError(f"{label} must be a UTC timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceRegistryError(f"{label} must be a UTC timestamp") from exc


def _validate_sha256(value: Any, label: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise SourceRegistryError(f"{label} must be a lowercase SHA-256 digest")
