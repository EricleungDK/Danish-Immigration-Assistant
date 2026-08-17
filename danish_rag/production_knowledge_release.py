"""Build a reviewed candidate release from completed official-source evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .evidence_integrity import is_utc_seconds
from .knowledge_release import (
    KnowledgeReleaseError,
    install_knowledge_release,
    load_active_release,
)
from .retrieval import HybridRetriever
from .source_freshness import assess_source_freshness
from .source_maintenance import build_publishable_knowledge_release
from .source_registry import (
    SourceRegistryError,
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


@dataclass(frozen=True)
class ReleaseGovernance:
    release_operator_ids: tuple[str, ...]
    release_approver_ids: tuple[str, ...]
    recovery_owner_ids: tuple[str, ...]
    recorded_at_utc: str

    def as_registry_evidence(self) -> dict[str, Any]:
        return {
            "release_operator_ids": list(self.release_operator_ids),
            "release_approver_ids": list(self.release_approver_ids),
            "recovery_owner_ids": list(self.recovery_owner_ids),
            "recorded_at_utc": self.recorded_at_utc,
        }


@dataclass(frozen=True)
class CandidateRetrievalQuery:
    id: str
    query_text: str
    required_source_ids: tuple[str, ...]
    forbidden_document_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReviewedSourceEvidence:
    source_id: str
    publisher: Any
    approved_url: Any
    topic: Any
    language: Any
    retrieved_at_utc: Any
    http_status: int
    snapshot_path: str
    snapshot_sha256: str
    extraction_path: str
    extraction_sha256: str
    extraction_schema_version: str
    normalized_content: str
    normalized_document_sha256: str
    curator_ids: Any
    admitted_at_utc: Any
    scope_rationale: Any
    monitoring_owner_ids: Any
    assessment_method: Any
    reviewed_at_utc: Any
    reviewer_ids: Any
    materiality: Any
    notes: Any
    interpretation_risks: Any
    second_reviewer_ids: Any
    staffing: Any
    single_maintainer_fallback: Any
    document_id: str
    title: str
    topic_tags: list[str]

    def registry_record(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "publisher": self.publisher,
            "official_url": self.approved_url,
            "topic": self.topic,
            "language": self.language,
            "registry_state": "approved-current",
            "content_origin": "official-source-normalized-extract",
            "production_release_eligible": True,
            "normalized_document_sha256": self.normalized_document_sha256,
            "curation_evidence": {
                "status": "completed",
                "curator_ids": self.curator_ids,
                "admitted_at_utc": self.admitted_at_utc,
                "scope_rationale": self.scope_rationale,
            },
            "monitoring_evidence": {
                "status": "recorded",
                "owner_ids": self.monitoring_owner_ids,
                "last_fetched_at_utc": self.retrieved_at_utc,
                "final_url": self.approved_url,
                "http_status": self.http_status,
            },
            "review_evidence": {
                "status": "completed",
                "assessment_method": self.assessment_method,
                "reviewed_at_utc": self.reviewed_at_utc,
                "reviewer_ids": self.reviewer_ids,
                "official_source_snapshot_path": self.snapshot_path,
                "official_source_snapshot_sha256": self.snapshot_sha256,
                "normalized_extraction_path": self.extraction_path,
                "normalized_extraction_sha256": self.extraction_sha256,
                "decision": "approved-current",
                "materiality": self.materiality,
                "notes": self.notes,
                "interpretation_risks": self.interpretation_risks,
                "second_reviewer_ids": self.second_reviewer_ids,
                "staffing": self.staffing,
                "single_maintainer_fallback": self.single_maintainer_fallback,
            },
        }

    def release_record(self, *, next_review_due_utc: str) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "publisher": self.publisher,
            "title": self.title,
            "official_url": self.approved_url,
            "final_url": self.approved_url,
            "topic": self.topic,
            "language": self.language,
            "review_state": "approved-current",
            "reviewed_at_utc": self.reviewed_at_utc,
            "reviewers": self.reviewer_ids,
            "last_checked_at_utc": self.retrieved_at_utc,
            "source_content_sha256": self.snapshot_sha256,
            "normalized_extraction_sha256": self.extraction_sha256,
            "normalized_document_sha256": self.normalized_document_sha256,
            "extraction_schema_version": self.extraction_schema_version,
            "fresh_tomato_inputs": {
                "next_review_due_utc": next_review_due_utc,
                "source_health": "current",
            },
        }

    def document_record(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source_id": self.source_id,
            "title": self.title,
            "publisher": self.publisher,
            "official_url": self.approved_url,
            "final_url": self.approved_url,
            "language": self.language,
            "topic_tags": self.topic_tags,
            "review_state": "approved-current",
            "source_health": "healthy",
            "approval_state": "approved",
            "checked_at_utc": self.retrieved_at_utc,
            "content_origin": "official-source-normalized-extract",
            "normalized_extraction_sha256": self.extraction_sha256,
            "content": self.normalized_content,
        }


DEFAULT_CANDIDATE_RETRIEVAL_QUERIES = (
    CandidateRetrievalQuery(
        id="permanent-residence-language-requirement",
        query_text="What Danish language test is required for permanent residence?",
        required_source_ids=("nyidanmark-permanent-residence-language-requirements",),
        forbidden_document_ids=("di-rag-doc-citizenship-language",),
    ),
    CandidateRetrievalQuery(
        id="language-test-2-equivalence",
        query_text=(
            "Is FVU reading exam level 2 or 3 equivalent to Danish language test 2?"
        ),
        required_source_ids=("nyidanmark-equivalent-tests-language-test-2",),
        forbidden_document_ids=("di-rag-doc-citizenship-language",),
    ),
    CandidateRetrievalQuery(
        id="language-test-3-equivalence",
        query_text=(
            "Does an International Baccalaureate with Danish A or B qualify for "
            "language test 3 equivalence?"
        ),
        required_source_ids=("nyidanmark-equivalent-tests-language-test-3",),
        forbidden_document_ids=("di-rag-doc-citizenship-language",),
    ),
    CandidateRetrievalQuery(
        id="danish-exam-overview",
        query_text=(
            "What are the Danish language examinations PD1 PD2 PD3 and Studieprøven?"
        ),
        required_source_ids=("danskogproever-danish-exam-overview",),
        forbidden_document_ids=("di-rag-doc-citizenship-language",),
    ),
    CandidateRetrievalQuery(
        id="registration-deadlines",
        query_text="When is the registration deadline for Danish language examinations?",
        required_source_ids=("danskogproever-registration-deadlines-2026",),
        forbidden_document_ids=("di-rag-doc-citizenship-language",),
    ),
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
    release_governance: ReleaseGovernance,
    signing_private_key_path: str | Path,
    trust_root_path: str | Path,
) -> CandidateReleaseBuild:
    """Build and cross-check one signed semantic-chunk candidate release."""

    resolved_review_dir = Path(review_dir)
    resolved_registry_path = Path(registry_path)
    resolved_release_dir = Path(release_dir)
    created_at = _require_utc_timestamp(created_at_utc, "created_at_utc")
    next_review_due = _require_utc_timestamp(
        next_review_due_utc,
        "next_review_due_utc",
    )
    if next_review_due <= created_at:
        raise KnowledgeReleaseError(
            "next_review_due_utc must be later than created_at_utc."
        )
    if release_governance.recorded_at_utc != created_at_utc:
        raise KnowledgeReleaseError(
            "Release governance must be recorded at candidate creation time."
        )
    completed_path = resolved_review_dir / "completed-review.json"
    bundle_path = resolved_review_dir / "review-bundle.json"
    decisions_path = resolved_review_dir / "human-decisions.json"
    completed = _load_json_object(completed_path, "completed source review")
    bundle, bundle_sha256 = _load_bound_json_object(
        bundle_path,
        completed.get("machine_review_manifest_sha256"),
        "machine review bundle",
    )
    decisions, decisions_sha256 = _load_bound_json_object(
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

    _validate_human_decision_binding(
        completed=completed,
        bundle_sha256=bundle_sha256,
        decisions=decisions,
    )

    completed_by_id = _unique_sources(completed.get("sources"), "completed review")
    bundle_by_id = _unique_sources(bundle.get("sources"), "review bundle")
    if set(completed_by_id) != set(SOURCE_DOCUMENT_METADATA) or set(
        bundle_by_id
    ) != set(completed_by_id):
        raise KnowledgeReleaseError(
            "Completed review must contain exactly the five configured official sources."
        )
    _require_release_not_before_review_evidence(
        created_at=created_at,
        completed_by_id=completed_by_id,
        bundle_by_id=bundle_by_id,
    )

    single_maintainer_fallback = completed.get("single_maintainer_fallback")
    registry_sources: list[dict[str, Any]] = []
    release_sources: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    for source_id in SOURCE_DOCUMENT_METADATA:
        completed_review = completed_by_id[source_id]
        machine_review_bundle = bundle_by_id[source_id]
        evidence = _load_reviewed_source_evidence(
            review_dir=resolved_review_dir,
            completed_review=completed_review,
            machine_review_bundle=machine_review_bundle,
            single_maintainer_fallback=single_maintainer_fallback,
        )
        registry_sources.append(evidence.registry_record())
        release_sources.append(
            evidence.release_record(next_review_due_utc=next_review_due_utc)
        )
        documents.append(evidence.document_record())

    registry = {
        "schema_version": "1.0",
        "source_registry_version": source_registry_version,
        "knowledge_release_id": release_id,
        "artifact_scope": "production-source-registry",
        "single_maintainer_fallback": single_maintainer_fallback,
        "release_governance": release_governance.as_registry_evidence(),
        "review_bundle_binding": {
            "completed_review_path": "completed-review.json",
            "machine_review_manifest_path": "review-bundle.json",
            "machine_review_manifest_sha256": bundle_sha256,
            "human_decisions_path": "human-decisions.json",
            "human_decisions_sha256": decisions_sha256,
        },
        "production_qualification": {
            "status": "qualified",
            "production_release_eligible": True,
            "reason_codes": [],
        },
        "sources": registry_sources,
    }
    try:
        qualification = assess_source_registry_qualification(registry)
    except SourceRegistryError as exc:
        raise KnowledgeReleaseError(
            f"Generated source registry is invalid: {exc}"
        ) from exc
    if not qualification["production_release_eligible"]:
        raise KnowledgeReleaseError(
            "Generated source registry is not production-qualified."
        )
    _claim_candidate_outputs(resolved_registry_path, resolved_release_dir)
    try:
        resolved_registry_path.write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        loaded_registry = load_source_registry(resolved_registry_path)
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
        validate_source_registry_against_release(
            loaded_registry,
            resolved_release_dir,
        )
    except Exception:
        resolved_registry_path.unlink(missing_ok=True)
        shutil.rmtree(resolved_release_dir, ignore_errors=True)
        raise
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
    embedding_provider: Any = None,
    embedding_model: str | None = None,
    embedding_endpoint: str | None = None,
    trust_root_path: str | Path,
    queries: tuple[CandidateRetrievalQuery, ...] = (
        DEFAULT_CANDIDATE_RETRIEVAL_QUERIES
    ),
) -> dict[str, Any]:
    """Qualify retrieval in isolation, then install and index the candidate."""

    if queries != DEFAULT_CANDIDATE_RETRIEVAL_QUERIES:
        raise KnowledgeReleaseError(
            "Candidate retrieval qualification requires the fixed five-case suite."
        )
    resolved_data_dir = Path(data_dir)
    resolved_data_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".candidate-release-qualification-",
        dir=resolved_data_dir.parent,
    ) as qualification_workspace:
        qualification_root = Path(qualification_workspace)
        pinned_release_dir = qualification_root / "candidate-release"
        shutil.copytree(release_dir, pinned_release_dir)
        qualification_dir = qualification_root / "local-data"
        qualification_installation = install_knowledge_release(
            qualification_dir,
            release_dir=pinned_release_dir,
            embedding_model=embedding_model,
            embedding_provider=embedding_provider,
            embedding_endpoint=embedding_endpoint,
            trust_root_path=trust_root_path,
        )
        report = _candidate_retrieval_report(
            data_dir=qualification_dir,
            installation=qualification_installation,
            embedding_provider=embedding_provider,
            embedding_endpoint=embedding_endpoint,
            trust_root_path=trust_root_path,
            queries=queries,
        )
        summary = report["summary"]
        if any(
            summary[field]
            for field in (
                "blocked_source_violations",
                "forbidden_result_violations",
                "required_source_misses",
            )
        ):
            raise KnowledgeReleaseError(
                "Candidate retrieval qualification failed: "
                f"{summary['blocked_source_violations']} blocked-source violation(s), "
                f"{summary['forbidden_result_violations']} forbidden-result violation(s), "
                f"and {summary['required_source_misses']} required-source miss(es)."
            )
        qualified_release_id = str(report["knowledge_release_id"])
        installation = install_knowledge_release(
            resolved_data_dir,
            release_dir=pinned_release_dir,
            embedding_model=embedding_model,
            embedding_provider=embedding_provider,
            embedding_endpoint=embedding_endpoint,
            trust_root_path=trust_root_path,
            expected_release_id=qualified_release_id,
        )
    report["knowledge_release_id"] = installation["manifest"]["knowledge_release_id"]
    return report


def _candidate_retrieval_report(
    *,
    data_dir: str | Path,
    installation: dict[str, Any],
    embedding_provider: Any,
    embedding_endpoint: str | None,
    trust_root_path: str | Path,
    queries: tuple[CandidateRetrievalQuery, ...],
) -> dict[str, Any]:
    retriever = HybridRetriever.from_data_dir(
        data_dir,
        embedding_provider=embedding_provider,
        embedding_endpoint=embedding_endpoint,
        trust_root_path=trust_root_path,
    )
    query_reports: list[dict[str, Any]] = []
    blocked_source_violations = 0
    forbidden_result_violations = 0
    required_source_misses = 0
    for query in queries:
        results = retriever.retrieve(query.query_text, limit=3)
        result_source_ids = {str(result["source_id"]) for result in results}
        required_source_ids = set(query.required_source_ids)
        forbidden_document_ids = set(query.forbidden_document_ids)
        blocked_results = [
            result
            for result in results
            if not assess_source_freshness(result).answer_eligible
        ]
        result_document_ids = {
            str(result.get("source_document_id", result["document_id"]))
            for result in results
        }
        forbidden_results = sorted(result_document_ids & forbidden_document_ids)
        missing_required = sorted(required_source_ids - result_source_ids)
        blocked_source_violations += len(blocked_results)
        forbidden_result_violations += len(forbidden_results)
        required_source_misses += len(missing_required)
        query_reports.append(
            {
                "id": query.id,
                "query_text": query.query_text,
                "required_source_ids": sorted(required_source_ids),
                "forbidden_document_ids": sorted(forbidden_document_ids),
                "missing_required_source_ids": missing_required,
                "forbidden_result_document_ids": forbidden_results,
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


def _load_reviewed_source_evidence(
    *,
    review_dir: Path,
    completed_review: dict[str, Any],
    machine_review_bundle: dict[str, Any],
    single_maintainer_fallback: Any,
) -> ReviewedSourceEvidence:
    source_id = str(completed_review.get("source_id", ""))
    human_review = completed_review.get("human_review", {})
    admission = completed_review.get("curator_admission", {})
    url_resolution = completed_review.get("url_resolution", {})
    retrieval = machine_review_bundle.get("retrieval", {})
    snapshot = machine_review_bundle.get("snapshot", {})
    extraction = machine_review_bundle.get("normalized_extraction", {})
    if (
        completed_review.get("eligible_for_follow_on_rebuild") is not True
        or human_review.get("decision") != "approve"
        or admission.get("decision") != "approve"
        or url_resolution.get("decision")
        not in {"approve-current", "approve-replacement"}
    ):
        raise KnowledgeReleaseError(
            f"Source {source_id} lacks completed approval evidence."
        )
    if machine_review_bundle.get("topic") != admission.get(
        "confirmed_topic"
    ) or machine_review_bundle.get("language") != admission.get("confirmed_language"):
        raise KnowledgeReleaseError(
            f"Source {source_id} admission differs from the reviewed bundle metadata."
        )
    approved_url = url_resolution.get("approved_url")
    http_status = retrieval.get("http_status")
    if (
        isinstance(http_status, bool)
        or not isinstance(http_status, int)
        or not 200 <= http_status < 300
    ):
        raise KnowledgeReleaseError(
            f"Source {source_id} lacks a successful reviewed retrieval."
        )
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
    _snapshot_bytes, snapshot_sha256 = _read_matching_artifact(
        snapshot_path,
        snapshot.get("sha256"),
        completed_review.get("official_source_snapshot_sha256"),
        human_review.get("official_source_snapshot_sha256"),
        label=f"source {source_id} snapshot",
    )
    extraction_bytes, extraction_sha256 = _read_matching_artifact(
        extraction_path,
        extraction.get("sha256"),
        completed_review.get("normalized_extraction_sha256"),
        human_review.get("normalized_extraction_sha256"),
        label=f"source {source_id} normalized extraction",
    )
    try:
        raw_content = extraction_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise KnowledgeReleaseError(
            f"Source {source_id} normalized extraction is not UTF-8."
        ) from exc
    normalized_content = " ".join(raw_content.split())
    normalized_document_sha256 = hashlib.sha256(
        normalized_content.encode("utf-8")
    ).hexdigest()
    metadata = SOURCE_DOCUMENT_METADATA[source_id]
    staffing = human_review.get("staffing")
    source_fallback = (
        single_maintainer_fallback
        if staffing == "mvp-single-maintainer-fallback"
        else None
    )
    extraction_schema_version = extraction.get("extraction_schema_version")
    if (
        not isinstance(extraction_schema_version, str)
        or not extraction_schema_version.strip()
    ):
        raise KnowledgeReleaseError(
            f"Source {source_id} lacks a valid extraction schema version."
        )
    return ReviewedSourceEvidence(
        source_id=source_id,
        publisher=machine_review_bundle.get("publisher"),
        approved_url=approved_url,
        topic=admission.get("confirmed_topic"),
        language=admission.get("confirmed_language"),
        retrieved_at_utc=retrieval.get("retrieved_at_utc"),
        http_status=http_status,
        snapshot_path=str(snapshot.get("path")),
        snapshot_sha256=snapshot_sha256,
        extraction_path=str(extraction.get("path")),
        extraction_sha256=extraction_sha256,
        extraction_schema_version=extraction_schema_version,
        normalized_content=normalized_content,
        normalized_document_sha256=normalized_document_sha256,
        curator_ids=admission.get("curator_ids"),
        admitted_at_utc=admission.get("admitted_at_utc"),
        scope_rationale=admission.get("scope_rationale"),
        monitoring_owner_ids=admission.get("monitoring_owner_ids"),
        assessment_method=human_review.get("assessment_method"),
        reviewed_at_utc=human_review.get("reviewed_at_utc"),
        reviewer_ids=human_review.get("reviewer_ids"),
        materiality=human_review.get("materiality"),
        notes=human_review.get("notes"),
        interpretation_risks=human_review.get("interpretation_risks"),
        second_reviewer_ids=human_review.get("second_reviewer_ids", []),
        staffing=staffing,
        single_maintainer_fallback=source_fallback,
        document_id=metadata["document_id"],
        title=metadata["title"],
        topic_tags=metadata["topic_tags"],
    )


def _validate_human_decision_binding(
    *,
    completed: dict[str, Any],
    bundle_sha256: str,
    decisions: dict[str, Any],
) -> None:
    review_bundle_binding = decisions.get("review_bundle")
    if (
        not isinstance(review_bundle_binding, dict)
        or review_bundle_binding.get("path") != "review-bundle.json"
        or review_bundle_binding.get("sha256") != bundle_sha256
    ):
        raise KnowledgeReleaseError(
            "Human decisions do not bind the reviewed machine bundle."
        )
    completed_by_id = _unique_sources(completed.get("sources"), "completed review")
    decisions_by_id = _unique_sources(
        decisions.get("source_decisions"),
        "human decisions",
    )
    if set(completed_by_id) != set(decisions_by_id):
        raise KnowledgeReleaseError(
            "Completed review source identities differ from bound human decisions."
        )
    decision_fields = (
        "curator_admission",
        "url_resolution",
        "human_review",
        "source_review_gate_status",
        "eligible_for_follow_on_rebuild",
    )
    for source_id, human_decision in decisions_by_id.items():
        completed_review = completed_by_id[source_id]
        if any(
            completed_review.get(field) != human_decision.get(field)
            for field in decision_fields
        ):
            raise KnowledgeReleaseError(
                f"Source {source_id} completed review differs from bound human decisions."
            )
    if completed.get("single_maintainer_fallback") != decisions.get(
        "single_maintainer_fallback"
    ):
        raise KnowledgeReleaseError(
            "Completed review fallback differs from bound human decisions."
        )


def _require_utc_timestamp(value: Any, label: str) -> datetime:
    if not is_utc_seconds(value):
        raise KnowledgeReleaseError(
            f"{label} must be a UTC timestamp at whole seconds."
        )
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _require_release_not_before_review_evidence(
    *,
    created_at: datetime,
    completed_by_id: dict[str, dict[str, Any]],
    bundle_by_id: dict[str, dict[str, Any]],
) -> None:
    for source_id, completed_review in completed_by_id.items():
        machine_review_bundle = bundle_by_id[source_id]
        timestamps = (
            (
                completed_review.get("curator_admission", {}).get("admitted_at_utc"),
                "curator admission",
            ),
            (
                completed_review.get("human_review", {}).get("reviewed_at_utc"),
                "human review",
            ),
            (
                machine_review_bundle.get("retrieval", {}).get("retrieved_at_utc"),
                "reviewed retrieval",
            ),
        )
        for value, event in timestamps:
            event_time = _require_utc_timestamp(
                value,
                f"Source {source_id} {event}",
            )
            if event_time > created_at:
                raise KnowledgeReleaseError(
                    "Candidate creation time cannot predate reviewed evidence."
                )


def _claim_candidate_outputs(registry_path: Path, release_dir: Path) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    release_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            registry_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise KnowledgeReleaseError(
            f"Candidate source registry already exists: {registry_path}"
        ) from exc
    os.close(descriptor)
    try:
        release_dir.mkdir(exist_ok=False)
    except FileExistsError as exc:
        registry_path.unlink(missing_ok=True)
        raise KnowledgeReleaseError(
            f"Candidate release directory already exists: {release_dir}"
        ) from exc
    except Exception:
        registry_path.unlink(missing_ok=True)
        raise


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    encoded = _read_file_bytes(path, label)
    return _decode_json_object(encoded, path=path, label=label)


def _load_bound_json_object(
    path: Path,
    expected_sha256: Any,
    label: str,
) -> tuple[dict[str, Any], str]:
    encoded = _read_file_bytes(path, label)
    actual_sha256 = hashlib.sha256(encoded).hexdigest()
    if actual_sha256 != expected_sha256:
        raise KnowledgeReleaseError(f"Completed review does not bind the {label}.")
    return (
        _decode_json_object(encoded, path=path, label=label),
        actual_sha256,
    )


def _read_file_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise KnowledgeReleaseError(f"Could not read {label}: {path}") from exc


def _decode_json_object(
    encoded: bytes,
    *,
    path: Path,
    label: str,
) -> dict[str, Any]:
    try:
        value = json.loads(
            encoded,
            object_pairs_hook=_reject_duplicate_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
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


def _read_matching_artifact(
    path: Path,
    *expected: Any,
    label: str,
) -> tuple[bytes, str]:
    encoded = _read_file_bytes(path, label)
    actual = hashlib.sha256(encoded).hexdigest()
    if not expected or any(value != actual for value in expected):
        raise KnowledgeReleaseError(f"Completed review does not bind the {label}.")
    return encoded, actual


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
    parser.add_argument("--release-operator", action="append", required=True)
    parser.add_argument("--release-approver", action="append", required=True)
    parser.add_argument("--recovery-owner", action="append", required=True)
    parser.add_argument("--signing-private-key", required=True)
    parser.add_argument("--trust-root", required=True)
    parser.add_argument("--install-data-dir", required=True)
    parser.add_argument("--embedding-model")
    parser.add_argument("--embedding-endpoint")
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
            release_governance=ReleaseGovernance(
                release_operator_ids=tuple(args.release_operator),
                release_approver_ids=tuple(args.release_approver),
                recovery_owner_ids=tuple(args.recovery_owner),
                recorded_at_utc=args.created_at_utc,
            ),
            signing_private_key_path=args.signing_private_key,
            trust_root_path=args.trust_root,
        )
        installation_report = install_and_verify_candidate(
            data_dir=args.install_data_dir,
            release_dir=candidate.release_dir,
            embedding_model=args.embedding_model,
            embedding_endpoint=args.embedding_endpoint,
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
                "installation": installation_report,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
