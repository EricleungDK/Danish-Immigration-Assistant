"""Maintainer tooling for source checks and reviewed knowledge releases."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .knowledge_release import KnowledgeReleaseError, verify_knowledge_release
from .release_trust import ReleaseTrustError, sign_manifest


ELIGIBLE_RELEASE_STATES = {"approved-current", "overdue-policy-usable"}
BLOCKED_RELEASE_STATES = {
    "discovered",
    "candidate-approved-url",
    "fetch-failed",
    "broken",
    "redirected-pending-review",
    "extraction-failed",
    "changed-unreviewed",
    "overdue-blocked",
    "withdrawn",
    "superseded",
}


def capture_source_check(
    registry_source: dict[str, Any],
    fetch_result: dict[str, Any],
    *,
    extracted_text: str | None,
    checked_at_utc: str,
    visible_dates: list[str] | None = None,
) -> dict[str, Any]:
    """Capture one automated source check without promoting changed content."""

    status_code = fetch_result.get("status_code")
    body = str(fetch_result.get("body", ""))
    final_url = str(fetch_result.get("final_url") or registry_source.get("official_url", ""))
    headers = {
        key.casefold(): str(value)
        for key, value in dict(fetch_result.get("headers", {})).items()
        if key.casefold() in {"etag", "last-modified", "content-type", "content-length"}
    }
    extraction_outcome = "succeeded" if extracted_text else "failed"
    source_hash = _sha256_text(body)
    normalized_text = _normalize_content(extracted_text or "")
    normalized_hash = _sha256_text(normalized_text)
    review_state = _review_state_for_check(
        registry_source,
        status_code=status_code,
        final_url=final_url,
        extraction_outcome=extraction_outcome,
        source_hash=source_hash,
        normalized_hash=normalized_hash,
        checked_at_utc=checked_at_utc,
    )
    release_eligible = review_state in ELIGIBLE_RELEASE_STATES
    if review_state == "approved-current":
        source_health = "healthy"
    else:
        source_health = review_state

    return {
        "source_id": registry_source["source_id"],
        "publisher": registry_source["publisher"],
        "title": registry_source.get("title", registry_source["source_id"]),
        "official_url": registry_source["official_url"],
        "final_url": final_url,
        "topic": registry_source["topic"],
        "language": registry_source["language"],
        "checked_at_utc": checked_at_utc,
        "last_checked_at_utc": checked_at_utc,
        "review_state": review_state,
        "source_health": source_health,
        "reviewed_at_utc": registry_source.get("reviewed_at_utc"),
        "reviewers": list(registry_source.get("reviewers", [])),
        "http": {
            "status_code": status_code,
            "final_url": final_url,
            "redirected": final_url != registry_source["official_url"],
            "metadata": headers,
        },
        "extraction": {
            "outcome": extraction_outcome,
            "schema_version": registry_source.get("extraction_schema_version", "1.0"),
        },
        "hashes": {
            "source_content_sha256": source_hash,
            "normalized_document_sha256": normalized_hash,
        },
        "source_content_sha256": source_hash,
        "normalized_document_sha256": normalized_hash,
        "extraction_schema_version": registry_source.get("extraction_schema_version", "1.0"),
        "visible_dates": list(visible_dates or []),
        "fresh_tomato_inputs": {
            **dict(registry_source.get("fresh_tomato_inputs", {})),
            "source_health": "current" if release_eligible else source_health,
        },
        "policy": {
            "release_eligible": release_eligible,
            "release_gate": _release_gate_for_state(review_state),
        },
    }


def approve_source_check(
    source_check: dict[str, Any],
    *,
    reviewer_id: str,
    reviewed_at_utc: str,
    next_review_due_utc: str,
) -> dict[str, Any]:
    """Record human approval for a checked source before release assembly."""

    if source_check.get("review_state") in BLOCKED_RELEASE_STATES - {"changed-unreviewed"}:
        raise KnowledgeReleaseError(
            f"Source {source_check.get('source_id', '<unknown>')} cannot be approved "
            f"from state {source_check.get('review_state')}."
        )
    approved = {
        key: value
        for key, value in source_check.items()
        if key not in {"http", "extraction", "hashes", "policy", "visible_dates"}
    }
    approved.update(
        {
            "review_state": "approved-current",
            "reviewed_at_utc": reviewed_at_utc,
            "reviewers": [reviewer_id],
            "fresh_tomato_inputs": {
                **dict(source_check.get("fresh_tomato_inputs", {})),
                "next_review_due_utc": next_review_due_utc,
                "source_health": "current",
            },
        }
    )
    return approved


def build_publishable_knowledge_release(
    *,
    release_dir: str | Path,
    release_id: str,
    source_registry_version: str,
    sources: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    created_at_utc: str,
    minimum_application_version: str,
    corpus_schema_version: str = "1.0",
    manifest_schema_version: str = "1.0",
    signing_private_key_path: str | Path | None = None,
    trust_root_path: str | Path | None = None,
) -> dict[str, Any]:
    """Write a release directory only from reviewed release-eligible sources."""

    for source in sources:
        if source.get("review_state") not in ELIGIBLE_RELEASE_STATES:
            raise KnowledgeReleaseError(
                f"Source {source.get('source_id', '<unknown>')} is not release-eligible."
            )
        if not source.get("reviewers"):
            raise KnowledgeReleaseError(
                f"Source {source.get('source_id', '<unknown>')} lacks human reviewer evidence."
            )

    if signing_private_key_path is None or trust_root_path is None:
        raise KnowledgeReleaseError(
            "Publishing a knowledge release requires an Ed25519 private signing key "
            "and an application-owned trust root."
        )
    try:
        trust_root = json.loads(Path(trust_root_path).read_text(encoding="utf-8"))
        trust_root_id = str(trust_root["trust_root_id"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise KnowledgeReleaseError("Release trust root metadata is invalid.") from exc
    if not trust_root_id:
        raise KnowledgeReleaseError("Release trust root ID is empty.")

    source_by_id = {str(source["source_id"]): source for source in sources}
    normalized_documents = [_release_document(document, source_by_id) for document in documents]
    documents_json = json.dumps(normalized_documents, indent=2, sort_keys=True) + "\n"
    artifact = {
        "path": "corpus/documents.json",
        "sha256": _sha256_text(documents_json),
        "bytes": len(documents_json.encode("utf-8")),
    }
    manifest = {
        "manifest_schema_version": manifest_schema_version,
        "knowledge_release_id": release_id,
        "created_at_utc": created_at_utc,
        "minimum_application_version": minimum_application_version,
        "corpus_schema_version": corpus_schema_version,
        "corpus_id": release_id,
        "source_registry_version": source_registry_version,
        "sources": [_manifest_source(source) for source in sources],
        "artifacts": [artifact],
        "integrity": {
            "hash_algorithm": "sha256",
            "signature_algorithm": "ed25519",
            "signature": "manifest.sig",
            "trust_root_id": trust_root_id,
        },
    }

    resolved_release_dir = Path(release_dir)
    corpus_dir = resolved_release_dir / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    (corpus_dir / "documents.json").write_text(documents_json, encoding="utf-8")
    manifest_path = resolved_release_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        sign_manifest(
            manifest_path,
            signing_private_key_path,
            resolved_release_dir / "manifest.sig",
        )
    except ReleaseTrustError as exc:
        raise KnowledgeReleaseError(f"Could not sign knowledge release: {exc}") from exc
    verify_knowledge_release(
        resolved_release_dir,
        trust_root_path=trust_root_path,
    )
    return {"manifest": manifest, "documents": normalized_documents, "artifact": artifact}


def _review_state_for_check(
    registry_source: dict[str, Any],
    *,
    status_code: Any,
    final_url: str,
    extraction_outcome: str,
    source_hash: str,
    normalized_hash: str,
    checked_at_utc: str,
) -> str:
    if registry_source.get("review_state") in {"discovered", "withdrawn", "superseded"}:
        return str(registry_source["review_state"])
    if status_code is None:
        return "fetch-failed"
    if int(status_code) >= 400:
        return "broken"
    if final_url != registry_source["official_url"]:
        return "redirected-pending-review"
    if extraction_outcome != "succeeded":
        return "extraction-failed"
    if registry_source.get("review_state") == "candidate-approved-url":
        return "changed-unreviewed"
    if (
        source_hash != registry_source.get("source_content_sha256")
        or normalized_hash != registry_source.get("normalized_document_sha256")
    ):
        return "changed-unreviewed"
    fresh_tomato_inputs = registry_source.get("fresh_tomato_inputs", {})
    blocked_at = fresh_tomato_inputs.get("overdue_blocked_after_utc")
    if blocked_at and _parse_utc(checked_at_utc) > _parse_utc(str(blocked_at)):
        return "overdue-blocked"
    due_at = fresh_tomato_inputs.get("next_review_due_utc")
    if due_at and _parse_utc(checked_at_utc) > _parse_utc(str(due_at)):
        return "overdue-policy-usable"
    return "approved-current"


def _release_gate_for_state(review_state: str) -> str:
    if review_state in ELIGIBLE_RELEASE_STATES:
        return "eligible after manifest verification"
    if review_state == "changed-unreviewed":
        return "blocked until human review approves the changed content"
    return f"blocked by {review_state} policy"


def _manifest_source(source: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "source_id",
        "publisher",
        "title",
        "official_url",
        "final_url",
        "topic",
        "language",
        "review_state",
        "reviewed_at_utc",
        "reviewers",
        "last_checked_at_utc",
        "source_content_sha256",
        "normalized_document_sha256",
        "extraction_schema_version",
        "fresh_tomato_inputs",
    }
    return {field: source[field] for field in sorted(fields) if field in source}


def _release_document(
    document: dict[str, Any],
    source_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    source_id = str(document.get("source_id", ""))
    source = source_by_id.get(source_id)
    if source is None:
        raise KnowledgeReleaseError(
            f"Document {document.get('document_id', '<unknown>')} references "
            "a source not in the manifest."
        )
    if document.get("review_state") not in ELIGIBLE_RELEASE_STATES:
        raise KnowledgeReleaseError(
            f"Document {document.get('document_id', '<unknown>')} is not release-eligible."
        )
    if document.get("approval_state") != "approved":
        raise KnowledgeReleaseError(
            f"Document {document.get('document_id', '<unknown>')} is missing approval."
        )
    return {
        **document,
        "publisher": document.get("publisher", source["publisher"]),
        "official_url": document.get("official_url", source["official_url"]),
        "final_url": document.get("final_url", source["final_url"]),
        "checked_at_utc": document.get("checked_at_utc", source["last_checked_at_utc"]),
    }


def _normalize_content(text: str) -> str:
    return " ".join(text.split())


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
