"""Build a reviewed candidate release from completed official-source evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .knowledge_release import (
    KnowledgeReleaseError,
    install_knowledge_release,
    load_active_release,
)
from .retrieval import HybridRetriever
from .source_maintenance import build_publishable_knowledge_release
from .source_registry import (
    assess_source_registry_qualification,
    load_source_registry,
    validate_source_registry_against_release,
)


SOURCE_DOCUMENT_METADATA: dict[str, dict[str, Any]] = {
    "nyidanmark-permanent-residence-language-requirements": {
        "document_id": "di-rag-doc-permanent-residence-language",
        "title": "Permanent residence requirements",
        "topic_tags": [
            "permanent-residence",
            "language-requirement",
            "exam-comparison",
            "certificate-equivalence",
        ],
    },
    "nyidanmark-equivalent-tests-language-test-2": {
        "document_id": "di-rag-doc-equivalent-tests-language-test-2",
        "title": "Tests equivalent to or higher than Danish language test 2",
        "topic_tags": [
            "permanent-residence",
            "language-requirement",
            "certificate-equivalence",
            "exam-comparison",
        ],
    },
    "nyidanmark-equivalent-tests-language-test-3": {
        "document_id": "di-rag-doc-equivalent-tests-language-test-3",
        "title": "Tests equivalent to or higher than Danish language test 3",
        "topic_tags": [
            "permanent-residence",
            "language-requirement",
            "certificate-equivalence",
            "exam-comparison",
        ],
    },
    "danskogproever-danish-exam-overview": {
        "document_id": "di-rag-doc-danish-exam-overview",
        "title": "Danish language examinations overview",
        "topic_tags": ["language-requirement", "exam", "exam-comparison"],
    },
    "danskogproever-registration-deadlines-2026": {
        "document_id": "di-rag-doc-registration-deadlines-2026",
        "title": "Registration deadlines and examination dates",
        "topic_tags": ["language-requirement", "registration-logistics", "exam"],
    },
}

DEFAULT_CANDIDATE_RETRIEVAL_QUERIES: tuple[dict[str, Any], ...] = (
    {
        "id": "permanent-residence-language-requirement",
        "query_text": "What Danish language test is required for permanent residence?",
        "required_source_ids": ["nyidanmark-permanent-residence-language-requirements"],
        "forbidden_source_ids": ["citizenship-language-requirements"],
    },
    {
        "id": "language-test-2-equivalence",
        "query_text": "Is FVU reading exam level 2 or 3 equivalent to Danish language test 2?",
        "required_source_ids": ["nyidanmark-equivalent-tests-language-test-2"],
        "forbidden_source_ids": ["citizenship-language-requirements"],
    },
    {
        "id": "language-test-3-equivalence",
        "query_text": "Does an International Baccalaureate with Danish A or B qualify for language test 3 equivalence?",
        "required_source_ids": ["nyidanmark-equivalent-tests-language-test-3"],
        "forbidden_source_ids": ["citizenship-language-requirements"],
    },
    {
        "id": "danish-exam-overview",
        "query_text": "What are the Danish language examinations PD1 PD2 PD3 and Studieprøven?",
        "required_source_ids": ["danskogproever-danish-exam-overview"],
        "forbidden_source_ids": ["citizenship-language-requirements"],
    },
    {
        "id": "registration-deadlines",
        "query_text": "When is the registration deadline for Danish language examinations?",
        "required_source_ids": ["danskogproever-registration-deadlines-2026"],
        "forbidden_source_ids": ["citizenship-language-requirements"],
    },
)


@dataclass(frozen=True)
class CandidateReleaseBuild:
    registry_path: Path
    release_dir: Path
    registry: dict[str, Any]
    release: dict[str, Any]


def build_reviewed_candidate_release(
    *,
    review_dir: str | Path,
    registry_path: str | Path,
    release_dir: str | Path,
    source_registry_version: str,
    release_id: str,
    created_at_utc: str,
    next_review_due_utc: str,
    signing_private_key_path: str | Path,
    trust_root_path: str | Path,
) -> CandidateReleaseBuild:
    """Build and cross-check one signed semantic-chunk candidate release."""

    resolved_review_dir = Path(review_dir)
    resolved_registry_path = Path(registry_path)
    resolved_release_dir = Path(release_dir)
    if resolved_registry_path.exists():
        raise KnowledgeReleaseError(
            f"Candidate source registry already exists: {resolved_registry_path}"
        )
    if resolved_release_dir.exists():
        raise KnowledgeReleaseError(
            f"Candidate release directory already exists: {resolved_release_dir}"
        )
    completed_path = resolved_review_dir / "completed-review.json"
    bundle_path = resolved_review_dir / "review-bundle.json"
    decisions_path = resolved_review_dir / "human-decisions.json"
    completed = _load_json_object(completed_path, "completed source review")
    bundle = _load_json_object(bundle_path, "source review bundle")

    _require_digest(
        bundle_path,
        completed.get("machine_review_manifest_sha256"),
        "machine review bundle",
    )
    _require_digest(
        decisions_path,
        completed.get("human_decisions_sha256"),
        "human decisions",
    )
    if completed.get("qualification_status") != (
        "source-review-complete-ready-for-follow-on-rebuild"
    ):
        raise KnowledgeReleaseError(
            "Completed source review is not eligible for rebuild."
        )
    if completed.get("packet_g", {}).get("status") != "unchanged":
        raise KnowledgeReleaseError(
            "Completed source review does not preserve packet G."
        )

    completed_by_id = _unique_sources(completed.get("sources"), "completed review")
    bundle_by_id = _unique_sources(bundle.get("sources"), "review bundle")
    if set(completed_by_id) != set(SOURCE_DOCUMENT_METADATA) or set(
        bundle_by_id
    ) != set(completed_by_id):
        raise KnowledgeReleaseError(
            "Completed review must contain exactly the five configured official sources."
        )

    fallback = completed.get("single_maintainer_fallback")
    registry_sources: list[dict[str, Any]] = []
    release_sources: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    for source_id in SOURCE_DOCUMENT_METADATA:
        reviewed = completed_by_id[source_id]
        machine = bundle_by_id[source_id]
        registry_source, release_source, document = _build_source_records(
            review_dir=resolved_review_dir,
            reviewed=reviewed,
            machine=machine,
            fallback=fallback,
            next_review_due_utc=next_review_due_utc,
        )
        registry_sources.append(registry_source)
        release_sources.append(release_source)
        documents.append(document)

    registry = {
        "schema_version": "1.0",
        "source_registry_version": source_registry_version,
        "knowledge_release_id": release_id,
        "artifact_scope": "production-source-registry",
        "single_maintainer_fallback": fallback,
        "review_bundle_binding": {
            "completed_review_path": "completed-review.json",
            "machine_review_manifest_path": "review-bundle.json",
            "machine_review_manifest_sha256": _sha256_file(bundle_path),
            "human_decisions_path": "human-decisions.json",
            "human_decisions_sha256": _sha256_file(decisions_path),
        },
        "production_qualification": {
            "status": "qualified",
            "production_release_eligible": True,
            "reason_codes": [],
        },
        "sources": registry_sources,
    }
    resolved_registry_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_registry_path.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    loaded_registry = load_source_registry(resolved_registry_path)
    qualification = assess_source_registry_qualification(loaded_registry)
    if not qualification["production_release_eligible"]:
        raise KnowledgeReleaseError(
            "Generated source registry is not production-qualified."
        )

    release = build_publishable_knowledge_release(
        release_dir=resolved_release_dir,
        release_id=release_id,
        source_registry_version=source_registry_version,
        sources=release_sources,
        documents=documents,
        created_at_utc=created_at_utc,
        minimum_application_version="0.1.0",
        corpus_schema_version="2.0",
        signing_private_key_path=signing_private_key_path,
        trust_root_path=trust_root_path,
    )
    validate_source_registry_against_release(loaded_registry, resolved_release_dir)
    return CandidateReleaseBuild(
        registry_path=resolved_registry_path,
        release_dir=resolved_release_dir,
        registry=loaded_registry,
        release=release,
    )


def install_and_verify_candidate(
    *,
    data_dir: str | Path,
    release_dir: str | Path,
    embedding_provider: Any,
    trust_root_path: str | Path,
    queries: tuple[dict[str, Any], ...] = DEFAULT_CANDIDATE_RETRIEVAL_QUERIES,
) -> dict[str, Any]:
    """Install, index, and verify candidate retrieval through the public API."""

    installation = install_knowledge_release(
        data_dir,
        release_dir=release_dir,
        embedding_provider=embedding_provider,
        trust_root_path=trust_root_path,
    )
    retriever = HybridRetriever.from_data_dir(
        data_dir,
        embedding_provider=embedding_provider,
        trust_root_path=trust_root_path,
    )
    query_reports: list[dict[str, Any]] = []
    blocked_source_violations = 0
    forbidden_result_violations = 0
    required_source_misses = 0
    for query in queries:
        results = retriever.retrieve(str(query["query_text"]), limit=3)
        result_source_ids = {str(result["source_id"]) for result in results}
        required_source_ids = set(query.get("required_source_ids", []))
        forbidden_source_ids = set(query.get("forbidden_source_ids", []))
        blocked_results = [
            result
            for result in results
            if result.get("review_state")
            not in {"approved-current", "overdue-policy-usable"}
            or result.get("source_health")
            in {
                "changed-unreviewed",
                "broken",
                "extraction-failed",
                "overdue-blocked",
                "withdrawn",
                "superseded",
            }
            or result.get("approval_state") != "approved"
        ]
        forbidden_results = sorted(result_source_ids & forbidden_source_ids)
        missing_required = sorted(required_source_ids - result_source_ids)
        blocked_source_violations += len(blocked_results)
        forbidden_result_violations += len(forbidden_results)
        required_source_misses += len(missing_required)
        query_reports.append(
            {
                "id": query["id"],
                "query_text": query["query_text"],
                "required_source_ids": sorted(required_source_ids),
                "forbidden_source_ids": sorted(forbidden_source_ids),
                "missing_required_source_ids": missing_required,
                "forbidden_result_source_ids": forbidden_results,
                "blocked_result_ids": sorted(
                    str(result["document_id"]) for result in blocked_results
                ),
                "results": results,
            }
        )
    manifest = installation["manifest"]
    return {
        "knowledge_release_id": manifest["knowledge_release_id"],
        "corpus_schema_version": manifest["corpus_schema_version"],
        "content_unit_schema_version": manifest.get("content_unit_schema_version"),
        "indexed_unit": installation["index"]["indexed_unit"],
        "indexed_chunk_count": len(retriever.release_documents),
        "summary": {
            "query_count": len(query_reports),
            "blocked_source_violations": blocked_source_violations,
            "forbidden_result_violations": forbidden_result_violations,
            "required_source_misses": required_source_misses,
        },
        "queries": query_reports,
    }


def verify_candidate_rollback_matrix(
    *,
    workspace: str | Path,
    prior_release_dir: str | Path,
    candidate_release_dir: str | Path,
    embedding_provider: Any,
    trust_root_path: str | Path,
) -> dict[str, Any]:
    """Inject every required install fault and prove the prior release survives."""

    phases = ("verification", "extraction", "embedding", "indexing", "activation")
    resolved_workspace = Path(workspace)
    cases: list[dict[str, Any]] = []
    for phase in phases:
        data_dir = resolved_workspace / phase
        prior = install_knowledge_release(
            data_dir,
            release_dir=prior_release_dir,
            embedding_provider=embedding_provider,
            trust_root_path=trust_root_path,
        )
        prior_release_id = str(prior["manifest"]["knowledge_release_id"])

        def inject(current_phase: str, *, expected_phase: str = phase) -> None:
            if current_phase == expected_phase:
                raise RuntimeError(f"simulated {expected_phase} failure")

        fault_observed = False
        try:
            install_knowledge_release(
                data_dir,
                release_dir=candidate_release_dir,
                embedding_provider=embedding_provider,
                trust_root_path=trust_root_path,
                fault_injector=inject,
            )
        except RuntimeError as exc:
            fault_observed = str(exc) == f"simulated {phase} failure"

        active = load_active_release(data_dir, trust_root_path=trust_root_path)
        prior_release_retained = (
            active["manifest"]["knowledge_release_id"] == prior_release_id
        )
        results = HybridRetriever.from_data_dir(
            data_dir,
            embedding_provider=embedding_provider,
            trust_root_path=trust_root_path,
        ).retrieve("What Danish language test is required for permanent residence?")
        prior_release_queryable = bool(results) and all(
            result["knowledge_release_id"] == prior_release_id for result in results
        )
        cases.append(
            {
                "phase": phase,
                "fault_observed": fault_observed,
                "prior_release_id": prior_release_id,
                "prior_release_retained": prior_release_retained,
                "prior_release_queryable": prior_release_queryable,
            }
        )
    return {
        "status": "passed"
        if all(
            case["fault_observed"]
            and case["prior_release_retained"]
            and case["prior_release_queryable"]
            for case in cases
        )
        else "failed",
        "cases": cases,
    }


def _build_source_records(
    *,
    review_dir: Path,
    reviewed: dict[str, Any],
    machine: dict[str, Any],
    fallback: Any,
    next_review_due_utc: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source_id = str(reviewed.get("source_id", ""))
    human_review = reviewed.get("human_review", {})
    admission = reviewed.get("curator_admission", {})
    url_resolution = reviewed.get("url_resolution", {})
    retrieval = machine.get("retrieval", {})
    snapshot = machine.get("snapshot", {})
    extraction = machine.get("normalized_extraction", {})
    if (
        reviewed.get("eligible_for_follow_on_rebuild") is not True
        or human_review.get("decision") != "approve"
        or admission.get("decision") != "approve"
        or url_resolution.get("decision")
        not in {"approve-current", "approve-replacement"}
    ):
        raise KnowledgeReleaseError(
            f"Source {source_id} lacks completed approval evidence."
        )
    if machine.get("topic") != admission.get("confirmed_topic") or machine.get(
        "language"
    ) != admission.get("confirmed_language"):
        raise KnowledgeReleaseError(
            f"Source {source_id} admission differs from the reviewed bundle metadata."
        )
    approved_url = url_resolution.get("approved_url")
    if approved_url != retrieval.get("final_url"):
        raise KnowledgeReleaseError(
            f"Source {source_id} approved URL differs from reviewed retrieval evidence."
        )

    snapshot_path = _review_artifact_path(review_dir, snapshot.get("path"), source_id)
    extraction_path = _review_artifact_path(
        review_dir,
        extraction.get("path"),
        source_id,
    )
    snapshot_sha256 = _require_matching_hashes(
        snapshot_path,
        snapshot.get("sha256"),
        reviewed.get("official_source_snapshot_sha256"),
        human_review.get("official_source_snapshot_sha256"),
        label=f"source {source_id} snapshot",
    )
    extraction_sha256 = _require_matching_hashes(
        extraction_path,
        extraction.get("sha256"),
        reviewed.get("normalized_extraction_sha256"),
        human_review.get("normalized_extraction_sha256"),
        label=f"source {source_id} normalized extraction",
    )
    raw_content = extraction_path.read_text(encoding="utf-8")
    normalized_content = " ".join(raw_content.split())
    normalized_document_sha256 = hashlib.sha256(
        normalized_content.encode("utf-8")
    ).hexdigest()
    metadata = SOURCE_DOCUMENT_METADATA[source_id]
    staffing = human_review.get("staffing")
    source_fallback = fallback if staffing == "mvp-single-maintainer-fallback" else None
    review_evidence = {
        "status": "completed",
        "assessment_method": human_review.get("assessment_method"),
        "reviewed_at_utc": human_review.get("reviewed_at_utc"),
        "reviewer_ids": human_review.get("reviewer_ids"),
        "official_source_snapshot_path": str(snapshot.get("path")),
        "official_source_snapshot_sha256": snapshot_sha256,
        "normalized_extraction_path": str(extraction.get("path")),
        "normalized_extraction_sha256": extraction_sha256,
        "decision": "approved-current",
        "materiality": human_review.get("materiality"),
        "notes": human_review.get("notes"),
        "interpretation_risks": human_review.get("interpretation_risks"),
        "second_reviewer_ids": (
            fallback.get("second_reviewer_ids", [])
            if isinstance(fallback, dict)
            else []
        ),
        "staffing": staffing,
        "single_maintainer_fallback": source_fallback,
    }
    registry_source = {
        "source_id": source_id,
        "publisher": machine.get("publisher"),
        "official_url": approved_url,
        "topic": admission.get("confirmed_topic"),
        "language": admission.get("confirmed_language"),
        "registry_state": "approved-current",
        "content_origin": "official-source-normalized-extract",
        "production_release_eligible": True,
        "normalized_document_sha256": normalized_document_sha256,
        "curation_evidence": {
            "status": "completed",
            "curator_ids": admission.get("curator_ids"),
            "admitted_at_utc": admission.get("admitted_at_utc"),
            "scope_rationale": admission.get("scope_rationale"),
        },
        "monitoring_evidence": {
            "status": "recorded",
            "owner_ids": admission.get("monitoring_owner_ids"),
            "last_fetched_at_utc": retrieval.get("retrieved_at_utc"),
            "final_url": retrieval.get("final_url"),
            "http_status": retrieval.get("http_status"),
        },
        "review_evidence": review_evidence,
    }
    release_source = {
        "source_id": source_id,
        "publisher": machine.get("publisher"),
        "title": metadata["title"],
        "official_url": approved_url,
        "final_url": retrieval.get("final_url"),
        "topic": admission.get("confirmed_topic"),
        "language": admission.get("confirmed_language"),
        "review_state": "approved-current",
        "reviewed_at_utc": human_review.get("reviewed_at_utc"),
        "reviewers": human_review.get("reviewer_ids"),
        "last_checked_at_utc": retrieval.get("retrieved_at_utc"),
        "source_content_sha256": snapshot_sha256,
        "normalized_extraction_sha256": extraction_sha256,
        "normalized_document_sha256": normalized_document_sha256,
        "extraction_schema_version": extraction.get("extraction_schema_version"),
        "fresh_tomato_inputs": {
            "next_review_due_utc": next_review_due_utc,
            "source_health": "current",
        },
    }
    document = {
        "document_id": metadata["document_id"],
        "source_id": source_id,
        "title": metadata["title"],
        "publisher": machine.get("publisher"),
        "official_url": approved_url,
        "final_url": retrieval.get("final_url"),
        "language": admission.get("confirmed_language"),
        "topic_tags": metadata["topic_tags"],
        "review_state": "approved-current",
        "source_health": "healthy",
        "approval_state": "approved",
        "checked_at_utc": retrieval.get("retrieved_at_utc"),
        "content_origin": "official-source-normalized-extract",
        "normalized_extraction_sha256": extraction_sha256,
        "content": normalized_content,
    }
    return registry_source, release_source, document


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_object,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeReleaseError(f"Could not read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise KnowledgeReleaseError(f"{label.capitalize()} must be a JSON object.")
    return value


def _unique_sources(value: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise KnowledgeReleaseError(f"{label.capitalize()} has no sources.")
    result: dict[str, dict[str, Any]] = {}
    for source in value:
        if not isinstance(source, dict) or not isinstance(source.get("source_id"), str):
            raise KnowledgeReleaseError(f"{label.capitalize()} has an invalid source.")
        source_id = source["source_id"]
        if source_id in result:
            raise KnowledgeReleaseError(f"{label.capitalize()} has duplicate sources.")
        result[source_id] = source
    return result


def _review_artifact_path(review_dir: Path, reference: Any, source_id: str) -> Path:
    if not isinstance(reference, str) or not reference:
        raise KnowledgeReleaseError(
            f"Source {source_id} has no reviewed artifact path."
        )
    candidate = review_dir / reference
    try:
        candidate.resolve().relative_to(review_dir.resolve())
    except (OSError, ValueError) as exc:
        raise KnowledgeReleaseError(
            f"Source {source_id} reviewed artifact path leaves the review bundle."
        ) from exc
    return candidate


def _require_digest(path: Path, expected: Any, label: str) -> None:
    if _sha256_file(path) != expected:
        raise KnowledgeReleaseError(f"Completed review does not bind the {label}.")


def _require_matching_hashes(
    path: Path,
    *expected: Any,
    label: str,
) -> str:
    actual = _sha256_file(path)
    if not expected or any(value != actual for value in expected):
        raise KnowledgeReleaseError(f"Completed review does not bind the {label}.")
    return actual


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise KnowledgeReleaseError(
            f"Could not read reviewed artifact: {path}"
        ) from exc


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", required=True)
    parser.add_argument("--registry-path", required=True)
    parser.add_argument("--release-dir", required=True)
    parser.add_argument("--source-registry-version", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--created-at-utc", required=True)
    parser.add_argument("--next-review-due-utc", required=True)
    parser.add_argument("--signing-private-key", required=True)
    parser.add_argument("--trust-root", required=True)
    args = parser.parse_args(argv)
    try:
        candidate = build_reviewed_candidate_release(
            review_dir=args.review_dir,
            registry_path=args.registry_path,
            release_dir=args.release_dir,
            source_registry_version=args.source_registry_version,
            release_id=args.release_id,
            created_at_utc=args.created_at_utc,
            next_review_due_utc=args.next_review_due_utc,
            signing_private_key_path=args.signing_private_key,
            trust_root_path=args.trust_root,
        )
    except KnowledgeReleaseError as exc:
        parser.exit(1, f"candidate release build failed: {exc}\n")
    print(
        json.dumps(
            {
                "knowledge_release_id": candidate.release["manifest"][
                    "knowledge_release_id"
                ],
                "source_registry_version": candidate.registry[
                    "source_registry_version"
                ],
                "source_count": len(candidate.registry["sources"]),
                "chunk_count": len(candidate.release["documents"]),
                "registry_path": str(candidate.registry_path),
                "release_dir": str(candidate.release_dir),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
