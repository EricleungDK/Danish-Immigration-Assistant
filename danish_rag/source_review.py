"""Archive official sources into a private bundle for human review."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

REVIEW_SET_SCHEMA_VERSION = "official-source-review-set-v1"
REVIEW_BUNDLE_SCHEMA_VERSION = "official-source-review-bundle-v1"
SUPPLEMENTAL_OBSERVATIONS_SCHEMA_VERSION = (
    "official-source-supplemental-observations-v1"
)
DEFAULT_CONFIG_PATH = Path("config/issue-46-official-source-review.json")
EXTRACTION_SCHEMA_VERSION = "visible-main-text-v1"
PACKET_G_QUALIFICATION_SCOPE = (
    "Packet G remains canonical only for kr-2026-07-06.1 and does not qualify "
    "a future replacement corpus."
)
_FORBIDDEN_FIXTURE_IDENTITIES = {"mvp-fixture-reviewer"}
_FORBIDDEN_FIXTURE_REVIEW_TIMESTAMPS = {
    "2026-07-06T00:00:00Z",
    "2026-07-06T12:00:00Z",
}
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
_IGNORED_TAGS = {"noscript", "script", "style", "svg", "template"}
_VISIBLE_DATE_PATTERN = re.compile(
    r"(?:sidst\s+opdateret|senest\s+opdateret|last\s+updated|opdateret|updated)"
    r"\s*:?\s*("
    r"\d{1,2}[-/.]\d{1,2}[-/.]\d{4}"
    r"|\d{1,2}\.?\s+[A-Za-zÆØÅæøå]+\s+\d{4}"
    r")",
    re.IGNORECASE,
)
_VISIBLE_PUBLISHER_PATTERN = re.compile(
    r"(?:publiceret\s+af|published\s+by)\s*:\s*([^\n]+)",
    re.IGNORECASE,
)


class SourceReviewError(ValueError):
    """Raised when an official-source review bundle cannot be produced safely."""


@dataclass(frozen=True)
class ValidatedReviewArtifact:
    relative_path: str
    sha256: str
    content: bytes


@dataclass(frozen=True)
class ValidatedSourceReviewEvidence:
    source_id: str
    completed_source: dict[str, Any]
    machine_source: dict[str, Any]
    snapshot: ValidatedReviewArtifact
    normalized_extraction: ValidatedReviewArtifact


@dataclass(frozen=True)
class CompletedSourceReviewValidation:
    completed_review: dict[str, Any]
    sources: dict[str, ValidatedSourceReviewEvidence]


class _VisibleMainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_main = False
        self.saw_main = False
        self.ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag == "main":
            self.in_main = True
            self.saw_main = True
        if not self.in_main:
            return
        if normalized_tag in _IGNORED_TAGS:
            self.ignored_depth += 1
            return
        if self.ignored_depth == 0 and normalized_tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if self.in_main and self.ignored_depth == 0 and tag.casefold() in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if not self.in_main:
            return
        if normalized_tag in _IGNORED_TAGS and self.ignored_depth:
            self.ignored_depth -= 1
            return
        if self.ignored_depth == 0 and normalized_tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if normalized_tag == "main":
            self.in_main = False

    def handle_data(self, data: str) -> None:
        if self.in_main and self.ignored_depth == 0:
            self.parts.append(data)

    def normalized_text(self) -> str:
        if not self.saw_main:
            raise SourceReviewError(
                "official source snapshot does not contain a visible main element"
            )
        lines = []
        for line in "".join(self.parts).splitlines():
            normalized_line = " ".join(line.split())
            if normalized_line:
                lines.append(normalized_line)
        if not lines:
            raise SourceReviewError(
                "official source main element contains no visible text"
            )
        return "\n".join(lines) + "\n"


class _RecordingRedirectHandler(HTTPRedirectHandler):
    def __init__(self, requested_url: str) -> None:
        super().__init__()
        self._requested_url = requested_url
        self.redirect_chain: list[dict[str, Any]] = []

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        resolved_url = urljoin(req.full_url, newurl)
        if not _same_origin(self._requested_url, resolved_url):
            raise SourceReviewError(
                f"official source redirected outside its origin: {resolved_url}"
            )
        self.redirect_chain.append(
            {
                "from_url": req.full_url,
                "http_status": code,
                "location": resolved_url,
            }
        )
        return super().redirect_request(req, fp, code, msg, headers, resolved_url)


def write_source_review_bundle(
    *,
    repo_root: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
    retrieved_at_utc: str,
) -> dict[str, Any]:
    """Fetch configured official pages and write their exact bytes outside the repo."""

    root = Path(repo_root).resolve()
    destination = Path(output_dir).expanduser().resolve()
    _require_outside_repository(destination, root)
    _parse_utc(retrieved_at_utc)
    review_set = _load_review_set(config_path)

    if destination.exists():
        raise SourceReviewError(f"review bundle output already exists: {destination}")
    snapshots_dir = destination / "snapshots"
    snapshots_dir.mkdir(parents=True, mode=0o700)
    normalized_dir = destination / "normalized"
    normalized_dir.mkdir(mode=0o700)
    review_pages_dir = destination / "review-pages"
    review_pages_dir.mkdir(mode=0o700)
    observations_dir = destination / "observations"
    observations_dir.mkdir(mode=0o700)

    sources = []
    for source in review_set["sources"]:
        registry_url_observation = None
        if source["registry_url"] != source["requested_url"]:
            observation_bytes, observation_retrieval = _fetch_source(
                str(source["registry_url"]),
                retrieved_at_utc=retrieved_at_utc,
                accept_http_error=True,
            )
            observation_path = (
                observations_dir / f"{source['source_id']}-registry-url.html"
            )
            _write_private_bytes(observation_path, observation_bytes)
            registry_url_observation = {
                **observation_retrieval,
                "response": {
                    "bytes": len(observation_bytes),
                    "path": observation_path.relative_to(destination).as_posix(),
                    "sha256": hashlib.sha256(observation_bytes).hexdigest(),
                },
            }
        snapshot_bytes, retrieval = _fetch_source(
            str(source["requested_url"]),
            retrieved_at_utc=retrieved_at_utc,
        )
        snapshot_path = snapshots_dir / f"{source['source_id']}.html"
        _write_private_bytes(snapshot_path, snapshot_bytes)
        snapshot_sha256 = hashlib.sha256(snapshot_bytes).hexdigest()
        normalized_bytes = _normalize_html(snapshot_bytes).encode("utf-8")
        normalized_path = normalized_dir / f"{source['source_id']}.txt"
        _write_private_bytes(normalized_path, normalized_bytes)
        normalized_text = normalized_bytes.decode("utf-8")
        offline_review_bytes = _offline_review_html(
            snapshot_bytes,
            source_id=str(source["source_id"]),
            snapshot_sha256=snapshot_sha256,
        )
        offline_review_path = review_pages_dir / f"{source['source_id']}.html"
        _write_private_bytes(offline_review_path, offline_review_bytes)
        sources.append(
            {
                "source_id": source["source_id"],
                "registry_url": source["registry_url"],
                "publisher": source["publisher"],
                "topic": source["topic"],
                "language": source["language"],
                "candidate_admission": {
                    "reason_for_inclusion": source["reason_for_inclusion"],
                    "in_scope_evidence": source["in_scope_evidence"],
                    "initial_owner_ids": source["initial_owner_ids"],
                    "human_status": "pending",
                },
                "retrieval": retrieval,
                "registry_url_observation": registry_url_observation,
                "snapshot": {
                    "bytes": len(snapshot_bytes),
                    "path": snapshot_path.relative_to(destination).as_posix(),
                    "sha256": snapshot_sha256,
                },
                "normalized_extraction": {
                    "bytes": len(normalized_bytes),
                    "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
                    "path": normalized_path.relative_to(destination).as_posix(),
                    "sha256": hashlib.sha256(normalized_bytes).hexdigest(),
                },
                "offline_review_page": {
                    "bytes": len(offline_review_bytes),
                    "derived_from_snapshot_sha256": snapshot_sha256,
                    "evidence_status": "derived-review-aid-not-source-snapshot",
                    "path": offline_review_path.relative_to(destination).as_posix(),
                    "sha256": hashlib.sha256(offline_review_bytes).hexdigest(),
                },
                "observed_metadata": {
                    "publisher": source["publisher"],
                    "visible_publisher": _visible_publisher(normalized_text),
                    "visible_source_date": _visible_source_date(normalized_text),
                },
                "curator_admission": {
                    "decision": None,
                    "curator_ids": [],
                    "admitted_at_utc": None,
                    "scope_rationale": None,
                    "topic": source["topic"],
                    "language": source["language"],
                    "monitoring_owner_ids": [],
                },
                "human_review": {
                    "decision": None,
                    "reviewer_ids": [],
                    "second_reviewer_ids": [],
                    "reviewed_at_utc": None,
                    "materiality": None,
                    "notes": None,
                    "interpretation_risks": [],
                    "single_maintainer_fallback": None,
                    "post_publication_review_due_utc": None,
                },
                "url_resolution": {
                    "registry_url": source["registry_url"],
                    "candidate_current_url": source["requested_url"],
                    "observed_final_url": retrieval["final_url"],
                    "status": (
                        "requires-human-resolution"
                        if source["registry_url"] != retrieval["final_url"]
                        else "unchanged"
                    ),
                    "human_decision": None,
                    "notes": None,
                },
                "review_flags": [
                    {
                        **flag,
                        "classification": "unverified-machine-observation",
                        "counts_as_human_review": False,
                    }
                    for flag in source["review_flags"]
                ],
                "production_release_eligible": False,
                "eligibility_status": "blocked-pending-human-review",
            }
        )

    bundle = {
        "schema_version": REVIEW_BUNDLE_SCHEMA_VERSION,
        "classification": "unreviewed-machine-evidence",
        "commit_policy": "do-not-commit-before-validated-human-review",
        "contains_human_decisions": False,
        "contains_official_source_snapshots": True,
        "network_fetch_performed": True,
        "production_release_eligible": False,
        "qualification_status": "blocked-pending-human-source-review",
        "retrieved_at_utc": retrieved_at_utc,
        "source_count": len(sources),
        "sources": sources,
    }
    _write_private_json(destination / "review-bundle.json", bundle)
    return bundle


def write_completed_source_review_bundle(
    *,
    machine_bundle_dir: str | Path,
    decisions_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Validate human decisions and copy hash-bound review evidence to a durable bundle."""

    machine_root = Path(machine_bundle_dir).resolve()
    machine_manifest_path = machine_root / "review-bundle.json"
    decision_source_path = Path(decisions_path).resolve()
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise SourceReviewError(
            f"completed review output already exists: {destination}"
        )
    try:
        destination.relative_to(machine_root)
    except ValueError:
        pass
    else:
        raise SourceReviewError(
            "completed review output cannot be inside the machine bundle"
        )

    machine_manifest_bytes = _read_bytes(
        machine_manifest_path,
        label="machine review manifest",
    )
    machine_manifest = _load_json_bytes(
        machine_manifest_bytes,
        label="machine review manifest",
    )
    supplemental_path = machine_root / "supplemental-observations.json"
    supplemental_bytes = (
        _read_bytes(supplemental_path, label="supplemental observations")
        if supplemental_path.exists()
        else None
    )
    supplemental_observations = (
        _load_json_bytes(supplemental_bytes, label="supplemental observations")
        if supplemental_bytes is not None
        else None
    )
    decision_bytes = _read_bytes(decision_source_path, label="human decisions")
    decisions = _load_json_bytes(decision_bytes, label="human decisions")
    validation = _validated_completed_review(
        machine_root=machine_root,
        machine_manifest=machine_manifest,
        machine_manifest_sha256=hashlib.sha256(machine_manifest_bytes).hexdigest(),
        supplemental_observations=supplemental_observations,
        supplemental_observations_sha256=(
            hashlib.sha256(supplemental_bytes).hexdigest()
            if supplemental_bytes is not None
            else None
        ),
        decisions=decisions,
        decisions_sha256=hashlib.sha256(decision_bytes).hexdigest(),
    )

    completed = validation.completed_review
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_path = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
    )
    try:
        shutil.copytree(machine_root, staging_path, dirs_exist_ok=True)
        _write_private_bytes(staging_path / "human-decisions.json", decision_bytes)
        _write_private_json(staging_path / "completed-review.json", completed)
        staging_path.replace(destination)
    except OSError as exc:
        raise SourceReviewError(
            f"could not write completed source-review bundle: {destination}"
        ) from exc
    finally:
        if staging_path.exists():
            shutil.rmtree(staging_path)
    return completed


def validate_completed_source_review_evidence(
    *,
    review_dir: str | Path,
    completed_review: dict[str, Any],
    machine_manifest: dict[str, Any],
    machine_manifest_sha256: str,
    decisions: dict[str, Any],
    decisions_sha256: str,
    supplemental_observations: dict[str, Any] | None = None,
    supplemental_observations_sha256: str | None = None,
) -> CompletedSourceReviewValidation:
    """Rebuild and compare the canonical completed-review contract."""

    validation = _validated_completed_review(
        machine_root=Path(review_dir).resolve(),
        machine_manifest=machine_manifest,
        machine_manifest_sha256=machine_manifest_sha256,
        supplemental_observations=supplemental_observations,
        supplemental_observations_sha256=supplemental_observations_sha256,
        decisions=decisions,
        decisions_sha256=decisions_sha256,
    )
    if validation.completed_review != completed_review:
        raise SourceReviewError(
            "completed review differs from the canonical validated review evidence"
        )
    return validation


def _validated_completed_review(
    *,
    machine_root: Path,
    machine_manifest: dict[str, Any],
    machine_manifest_sha256: str,
    supplemental_observations: dict[str, Any] | None,
    supplemental_observations_sha256: str | None,
    decisions: dict[str, Any],
    decisions_sha256: str,
) -> CompletedSourceReviewValidation:
    if machine_manifest.get("schema_version") != REVIEW_BUNDLE_SCHEMA_VERSION:
        raise SourceReviewError("machine review manifest has an invalid schema version")
    if decisions.get("schema_version") != "official-source-human-decisions-v1":
        raise SourceReviewError("human decisions have an invalid schema version")
    recorded_at = _parse_human_review_utc(decisions.get("recorded_at_utc"))
    decision_bundle = decisions.get("review_bundle")
    if (
        not isinstance(decision_bundle, dict)
        or decision_bundle.get("sha256") != machine_manifest_sha256
    ):
        raise SourceReviewError(
            "human decisions do not bind the machine review manifest"
        )

    machine_sources = machine_manifest.get("sources")
    decision_sources = decisions.get("source_decisions")
    if not isinstance(machine_sources, list) or not isinstance(decision_sources, list):
        raise SourceReviewError("completed review sources must be arrays")
    machine_by_id = _unique_sources(machine_sources, label="machine review")
    decisions_by_id = _unique_sources(decision_sources, label="human decisions")
    if set(machine_by_id) != set(decisions_by_id):
        raise SourceReviewError(
            "human decisions must cover every machine review source exactly once"
        )
    supplemental_by_id = _validated_supplemental_observations(
        machine_root,
        supplemental_observations,
        allowed_source_ids=set(machine_by_id),
    )

    identity = decisions.get("human_identity")
    if not isinstance(identity, dict):
        raise SourceReviewError("human decisions are missing named identities")
    for field in ("curator_ids", "monitoring_owner_ids", "reviewer_ids"):
        _require_named_ids(identity.get(field), label=field)

    fallback = decisions.get("single_maintainer_fallback")
    if not isinstance(fallback, dict):
        raise SourceReviewError("human decisions are missing staffing evidence")
    fallback_selected = fallback.get("selected") is True
    post_publication = fallback.get("post_publication_second_review")
    fallback_review_required = (
        isinstance(post_publication, dict) and post_publication.get("required") is True
    )
    if fallback_selected:
        if (
            not isinstance(fallback.get("reason"), str)
            or not fallback["reason"].strip()
        ):
            raise SourceReviewError(
                "single-maintainer fallback requires a documented reason"
            )
        if (
            not fallback_review_required
            or post_publication.get("status") != "pending-publication"
            or not isinstance(post_publication.get("schedule"), str)
            or not post_publication["schedule"].strip()
        ):
            raise SourceReviewError(
                "single-maintainer fallback requires a documented post-publication "
                "second review"
            )

    completed_sources = []
    validated_sources: dict[str, ValidatedSourceReviewEvidence] = {}
    approved_source_count = 0
    for source_id, machine_source in machine_by_id.items():
        record = decisions_by_id[source_id]
        curation = record.get("curator_admission")
        review = record.get("human_review")
        if not isinstance(curation, dict) or not isinstance(review, dict):
            raise SourceReviewError(
                f"source {source_id} is missing curation or review evidence"
            )
        if curation.get("decision") not in {"approve", "reject"}:
            raise SourceReviewError(
                f"source {source_id} has an invalid curator decision"
            )
        admitted_at = _parse_human_review_utc(curation.get("admitted_at_utc"))
        if admitted_at > recorded_at:
            raise SourceReviewError(
                f"source {source_id} admission postdates the human decision record"
            )
        for field in ("scope_rationale", "confirmed_topic", "confirmed_language"):
            if not isinstance(curation.get(field), str) or not curation[field].strip():
                raise SourceReviewError(f"source {source_id} curation requires {field}")
        _require_named_ids(
            curation.get("curator_ids"),
            label=f"source {source_id} curator_ids",
        )
        _require_named_ids(
            curation.get("monitoring_owner_ids"),
            label=f"source {source_id} monitoring_owner_ids",
        )
        if review.get("decision") not in {"approve", "reject"}:
            raise SourceReviewError(
                f"source {source_id} has an invalid review decision"
            )
        reviewed_at = _parse_human_review_utc(review.get("reviewed_at_utc"))
        if reviewed_at > recorded_at:
            raise SourceReviewError(
                f"source {source_id} review postdates the human decision record"
            )
        reviewer_ids = _require_named_ids(
            review.get("reviewer_ids"),
            label=f"source {source_id} reviewer_ids",
        )
        if review.get("materiality") not in {"material", "non-material"}:
            raise SourceReviewError(f"source {source_id} has invalid materiality")
        if not isinstance(review.get("notes"), str) or not review["notes"].strip():
            raise SourceReviewError(f"source {source_id} review requires notes")
        risks = review.get("interpretation_risks")
        if not isinstance(risks, list) or any(
            not isinstance(risk, str) or not risk.strip() for risk in risks
        ):
            raise SourceReviewError(
                f"source {source_id} has invalid interpretation risks"
            )

        snapshot = machine_source.get("snapshot")
        extraction = machine_source.get("normalized_extraction")
        validated_snapshot = _validated_artifact(
            machine_root,
            snapshot,
            label=f"source {source_id} snapshot",
        )
        validated_extraction = _validated_artifact(
            machine_root,
            extraction,
            label=f"source {source_id} normalized extraction",
        )
        if review.get("official_source_snapshot_sha256") != validated_snapshot.sha256:
            raise SourceReviewError(
                f"source {source_id} review does not bind its official snapshot"
            )
        if review.get("normalized_extraction_sha256") != validated_extraction.sha256:
            raise SourceReviewError(
                f"source {source_id} review does not bind its normalized extraction"
            )
        retrieval = _validated_retrieval(
            machine_source.get("retrieval"),
            label=f"source {source_id} retrieval",
        )
        if retrieval["retrieved_at"] > reviewed_at:
            raise SourceReviewError(
                f"source {source_id} review predates its retrieved evidence"
            )

        bound_registry_url_observation = machine_source.get("registry_url_observation")
        if bound_registry_url_observation is not None:
            if not isinstance(bound_registry_url_observation, dict):
                raise SourceReviewError(
                    f"source {source_id} registry URL observation is invalid"
                )
            registry_retrieval = _validated_retrieval(
                bound_registry_url_observation,
                label=f"source {source_id} registry URL retrieval",
            )
            if registry_retrieval["retrieved_at"] > reviewed_at:
                raise SourceReviewError(
                    f"source {source_id} review predates its registry URL evidence"
                )
            _validated_artifact(
                machine_root,
                bound_registry_url_observation.get("response"),
                label=f"source {source_id} registry URL response",
            )
        supplemental_registry_observation = supplemental_by_id.get(source_id)
        registry_url_evidence = bound_registry_url_observation
        if (
            registry_url_evidence is None
            and supplemental_registry_observation is not None
        ):
            registry_url_evidence = supplemental_registry_observation["retrieval"]

        url_resolution = record.get("url_resolution")
        if not isinstance(url_resolution, dict):
            raise SourceReviewError(
                f"source {source_id} requires a resolved URL decision"
            )
        url_decision = url_resolution.get("decision")
        if url_decision not in {"approve-current", "approve-replacement", "reject"}:
            raise SourceReviewError(
                f"source {source_id} requires a resolved URL decision"
            )
        url_approved = url_decision in {"approve-current", "approve-replacement"}
        if url_approved:
            approved_url = url_resolution.get("approved_url")
            if (
                not isinstance(approved_url, str)
                or approved_url != retrieval["final_url"]
            ):
                raise SourceReviewError(
                    f"source {source_id} approved URL does not match retrieved evidence"
                )
            registry_url = machine_source.get("registry_url")
            if not isinstance(registry_url, str) or not registry_url:
                raise SourceReviewError(
                    f"source {source_id} is missing its registry URL"
                )
            if url_decision == "approve-current" and registry_url != approved_url:
                raise SourceReviewError(
                    f"source {source_id} current URL approval conflicts with provenance"
                )
            if url_decision == "approve-replacement":
                if (
                    registry_url == approved_url
                    or url_resolution.get("superseded_registry_url") != registry_url
                    or not isinstance(registry_url_evidence, dict)
                    or registry_url_evidence.get("requested_url") != registry_url
                ):
                    raise SourceReviewError(
                        f"source {source_id} replacement URL approval lacks bound "
                        "registry provenance"
                    )

        second_reviewer_ids = review.get("second_reviewer_ids", [])
        if second_reviewer_ids:
            _require_named_ids(
                second_reviewer_ids,
                label=f"source {source_id} second_reviewer_ids",
            )
        distinct_reviewers = {*reviewer_ids, *second_reviewer_ids}
        if (
            review["materiality"] == "material"
            and len(distinct_reviewers) < 2
            and not (fallback_selected and fallback_review_required)
        ):
            raise SourceReviewError(
                f"source {source_id} material review lacks second-reviewer evidence "
                "or the documented MVP fallback"
            )

        approved = (
            curation["decision"] == "approve"
            and review["decision"] == "approve"
            and url_approved
            and record.get("eligible_for_follow_on_rebuild") is True
        )
        if record.get("eligible_for_follow_on_rebuild") is True and not approved:
            raise SourceReviewError(
                f"source {source_id} claims eligibility without all required approvals"
            )
        approved_source_count += int(approved)
        completed_source = {
            "source_id": source_id,
            "curator_admission": curation,
            "url_resolution": url_resolution,
            "human_review": review,
            "official_source_snapshot_sha256": validated_snapshot.sha256,
            "normalized_extraction_sha256": validated_extraction.sha256,
            "registry_url_observation": bound_registry_url_observation,
            "supplemental_registry_evidence": (
                source_id if supplemental_registry_observation is not None else None
            ),
            "source_review_gate_status": record.get("source_review_gate_status"),
            "eligible_for_follow_on_rebuild": approved,
        }
        completed_sources.append(completed_source)
        validated_sources[source_id] = ValidatedSourceReviewEvidence(
            source_id=source_id,
            completed_source=completed_source,
            machine_source=machine_source,
            snapshot=validated_snapshot,
            normalized_extraction=validated_extraction,
        )

    packet_g = decisions.get("packet_g")
    if (
        not isinstance(packet_g, dict)
        or packet_g.get("knowledge_release_id") != "kr-2026-07-06.1"
        or packet_g.get("status") != "unchanged"
        or packet_g.get("qualification_scope") != PACKET_G_QUALIFICATION_SCOPE
    ):
        raise SourceReviewError(
            "completed review must preserve packet G for kr-2026-07-06.1"
        )

    blocked_source_count = len(completed_sources) - approved_source_count
    completed_review = {
        "schema_version": "completed-official-source-review-v1",
        "classification": "reviewed-official-source-evidence",
        "commit_policy": "version-control-eligible",
        "qualification_status": (
            "source-review-complete-ready-for-follow-on-rebuild"
            if blocked_source_count == 0
            else "source-review-complete-with-blocked-sources"
        ),
        "recorded_at_utc": decisions["recorded_at_utc"],
        "machine_review_manifest_sha256": machine_manifest_sha256,
        "human_decisions_sha256": decisions_sha256,
        "supplemental_observations_sha256": supplemental_observations_sha256,
        "supplemental_observations": (
            supplemental_observations.get("observations", [])
            if supplemental_observations is not None
            else []
        ),
        "approved_source_count": approved_source_count,
        "blocked_source_count": blocked_source_count,
        "sources": completed_sources,
        "single_maintainer_fallback": fallback,
        "packet_g": packet_g,
        "follow_on_rebuild_required": True,
        "current_fixture_release_qualified": False,
    }
    return CompletedSourceReviewValidation(
        completed_review=completed_review,
        sources=validated_sources,
    )


def _unique_sources(sources: list[Any], *, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise SourceReviewError(f"{label} source entries must be objects")
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not source_id or source_id in result:
            raise SourceReviewError(
                f"{label} source IDs must be unique non-empty strings"
            )
        result[source_id] = source
    return result


def _require_named_ids(value: Any, *, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(
            not isinstance(identity, str) or not identity.strip() for identity in value
        )
    ):
        raise SourceReviewError(f"{label} requires named human identities")
    if any(
        identity.strip().casefold() in _FORBIDDEN_FIXTURE_IDENTITIES
        for identity in value
    ):
        raise SourceReviewError(f"{label} cannot use fixture reviewer identities")
    return value


def _parse_human_review_utc(value: Any) -> datetime:
    if value in _FORBIDDEN_FIXTURE_REVIEW_TIMESTAMPS:
        raise SourceReviewError(
            "fixture timestamps cannot be used as human review evidence"
        )
    return _parse_utc(value)


def _validated_supplemental_observations(
    machine_root: Path,
    payload: dict[str, Any] | None,
    *,
    allowed_source_ids: set[str],
) -> dict[str, dict[str, Any]]:
    if payload is None:
        return {}
    if payload.get("schema_version") != SUPPLEMENTAL_OBSERVATIONS_SCHEMA_VERSION:
        raise SourceReviewError("supplemental observations have an invalid schema")
    _parse_utc(payload.get("recorded_at_utc"))
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise SourceReviewError("supplemental observations must be an array")
    by_source_id: dict[str, dict[str, Any]] = {}
    for observation in observations:
        if not isinstance(observation, dict):
            raise SourceReviewError("supplemental observation entries must be objects")
        source_id = observation.get("source_id")
        if (
            not isinstance(source_id, str)
            or source_id not in allowed_source_ids
            or source_id in by_source_id
        ):
            raise SourceReviewError(
                "supplemental observations require unique known source IDs"
            )
        if (
            observation.get("kind") != "superseded-registry-url-response"
            or observation.get("evidence_status")
            != "post-review-machine-observation-not-human-review"
        ):
            raise SourceReviewError(
                f"source {source_id} supplemental observation is not classified safely"
            )
        retrieval = observation.get("retrieval")
        _validated_retrieval(
            retrieval,
            label=f"source {source_id} supplemental registry retrieval",
        )
        if not isinstance(retrieval, dict):
            raise SourceReviewError(
                f"source {source_id} supplemental registry retrieval is invalid"
            )
        _validated_artifact(
            machine_root,
            retrieval.get("response"),
            label=f"source {source_id} supplemental registry response",
        )
        by_source_id[source_id] = observation
    return by_source_id


def _validated_retrieval(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SourceReviewError(f"{label} metadata is missing")
    requested_url = value.get("requested_url")
    final_url = value.get("final_url")
    http_status = value.get("http_status")
    if (
        not isinstance(requested_url, str)
        or not requested_url
        or not isinstance(final_url, str)
        or not final_url
        or not isinstance(http_status, int)
    ):
        raise SourceReviewError(f"{label} metadata is invalid")
    return {
        "requested_url": requested_url,
        "final_url": final_url,
        "http_status": http_status,
        "retrieved_at": _parse_utc(value.get("retrieved_at_utc")),
    }


def _validated_artifact(
    machine_root: Path,
    artifact: Any,
    *,
    label: str,
) -> ValidatedReviewArtifact:
    if not isinstance(artifact, dict):
        raise SourceReviewError(f"{label} metadata is missing")
    relative_path = artifact.get("path")
    expected_sha256 = artifact.get("sha256")
    if not isinstance(relative_path, str) or not isinstance(expected_sha256, str):
        raise SourceReviewError(f"{label} metadata is invalid")
    artifact_path = (machine_root / relative_path).resolve()
    try:
        artifact_path.relative_to(machine_root)
    except ValueError as exc:
        raise SourceReviewError(f"{label} path leaves the machine bundle") from exc
    content = _read_bytes(artifact_path, label=label)
    observed_sha256 = hashlib.sha256(content).hexdigest()
    if observed_sha256 != expected_sha256:
        raise SourceReviewError(f"{label} SHA-256 does not match")
    return ValidatedReviewArtifact(
        relative_path=relative_path,
        sha256=observed_sha256,
        content=content,
    )


def _read_bytes(path: Path, *, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SourceReviewError(f"could not read {label}: {path}") from exc


def _load_json_bytes(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceReviewError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SourceReviewError(f"{label} must be a JSON object")
    return value


def _normalize_html(snapshot_bytes: bytes) -> str:
    try:
        html = snapshot_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceReviewError(
            "official source snapshot is not valid UTF-8 HTML"
        ) from exc
    parser = _VisibleMainTextParser()
    parser.feed(html)
    parser.close()
    return parser.normalized_text()


def _offline_review_html(
    snapshot_bytes: bytes,
    *,
    source_id: str,
    snapshot_sha256: str,
) -> bytes:
    try:
        snapshot_html = snapshot_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceReviewError(
            "official source snapshot is not valid UTF-8 HTML"
        ) from exc

    content_security_policy = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:;"
    )
    head_injection = (
        "\n"
        '<meta http-equiv="Content-Security-Policy" '
        f'content="{escape(content_security_policy, quote=True)}">\n'
        '<style id="di-rag-offline-review-style">\n'
        "html { visibility: visible !important; opacity: 1 !important; }\n"
        "body, main, main * { visibility: visible !important; opacity: 1 !important; }\n"
        "body { color: #111 !important; background: #fff !important; }\n"
        "#di-rag-offline-review-banner { padding: 12px; border: 2px solid #555; "
        "font: 16px/1.4 sans-serif; color: #111; background: #fff4cc; }\n"
        "</style>\n"
    )
    body_injection = (
        "\n"
        '<div id="di-rag-offline-review-banner">'
        "Offline review copy derived from the archived official snapshot. "
        "This page is a review aid, not the source evidence. "
        f"Source: {escape(source_id)}. "
        f"Snapshot SHA-256: {escape(snapshot_sha256)}."
        "</div>\n"
    )

    with_head = re.sub(
        r"(<head(?:\s[^>]*)?>)",
        lambda match: match.group(1) + head_injection,
        snapshot_html,
        count=1,
        flags=re.IGNORECASE,
    )
    if with_head == snapshot_html:
        raise SourceReviewError(
            "official source snapshot does not contain a head element"
        )
    with_body = re.sub(
        r"(<body(?:\s[^>]*)?>)",
        lambda match: match.group(1) + body_injection,
        with_head,
        count=1,
        flags=re.IGNORECASE,
    )
    if with_body == with_head:
        raise SourceReviewError(
            "official source snapshot does not contain a body element"
        )
    return with_body.encode("utf-8")


def _visible_source_date(normalized_text: str) -> str | None:
    match = _VISIBLE_DATE_PATTERN.search(normalized_text)
    return match.group(1) if match else None


def _visible_publisher(normalized_text: str) -> str | None:
    match = _VISIBLE_PUBLISHER_PATTERN.search(normalized_text)
    return match.group(1).strip() if match else None


def _load_review_set(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SourceReviewError(f"could not read review set: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SourceReviewError(f"review set is not valid JSON: {path}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != REVIEW_SET_SCHEMA_VERSION
    ):
        raise SourceReviewError(
            f"review set must use schema version {REVIEW_SET_SCHEMA_VERSION}"
        )
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise SourceReviewError("review set must contain at least one source")
    required = {
        "source_id",
        "registry_url",
        "requested_url",
        "publisher",
        "topic",
        "language",
        "reason_for_inclusion",
        "in_scope_evidence",
        "initial_owner_ids",
        "review_flags",
    }
    seen_ids: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, dict) or set(source) != required:
            raise SourceReviewError(f"review set source {index} has an invalid shape")
        string_fields = required - {"initial_owner_ids", "review_flags"}
        if any(
            not isinstance(source[field], str) or not source[field].strip()
            for field in string_fields
        ):
            raise SourceReviewError(f"review set source {index} contains a blank field")
        source_id = source["source_id"]
        if source_id in seen_ids:
            raise SourceReviewError(
                f"review set contains duplicate source ID: {source_id}"
            )
        seen_ids.add(source_id)
        _validate_fetch_url(source["requested_url"])
        _validate_fetch_url(source["registry_url"])
        _require_named_ids(
            source["initial_owner_ids"],
            label=f"review set source {source_id} initial_owner_ids",
        )
        _validate_review_flags(source_id, source["review_flags"])
    return payload


def _validate_review_flags(source_id: str, flags: Any) -> None:
    if not isinstance(flags, list):
        raise SourceReviewError(f"review set source {source_id} flags must be a list")
    required = {
        "code",
        "summary",
        "requires_human_materiality_decision",
    }
    for index, flag in enumerate(flags):
        if not isinstance(flag, dict) or set(flag) != required:
            raise SourceReviewError(
                f"review set source {source_id} flag {index} has an invalid shape"
            )
        if (
            not isinstance(flag["code"], str)
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", flag["code"]) is None
            or not isinstance(flag["summary"], str)
            or not flag["summary"].strip()
            or not isinstance(flag["requires_human_materiality_decision"], bool)
        ):
            raise SourceReviewError(
                f"review set source {source_id} flag {index} is invalid"
            )


def _fetch_source(
    requested_url: str,
    *,
    retrieved_at_utc: str,
    accept_http_error: bool = False,
) -> tuple[bytes, dict[str, Any]]:
    redirect_handler = _RecordingRedirectHandler(requested_url)
    opener = build_opener(redirect_handler)
    request = Request(
        requested_url,
        headers={"User-Agent": "Danish-Immigration-RAG-source-review/1.0"},
    )
    try:
        with opener.open(request, timeout=30) as response:
            snapshot_bytes = response.read()
            final_url = response.geturl()
            http_status = response.status
    except HTTPError as exc:
        if not accept_http_error:
            raise SourceReviewError(
                f"official source returned HTTP {exc.code}: {requested_url}"
            ) from exc
        snapshot_bytes = exc.read()
        final_url = exc.geturl()
        http_status = exc.code
    except SourceReviewError:
        raise
    except OSError as exc:
        raise SourceReviewError(
            f"could not fetch official source: {requested_url}"
        ) from exc
    return snapshot_bytes, {
        "requested_url": requested_url,
        "redirect_chain": redirect_handler.redirect_chain,
        "final_url": final_url,
        "retrieved_at_utc": retrieved_at_utc,
        "http_status": http_status,
    }


def _validate_fetch_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.username or parsed.password or parsed.fragment:
        raise SourceReviewError(f"official source URL is not safe to fetch: {value}")
    is_https = parsed.scheme == "https" and parsed.port in {None, 443}
    is_loopback_http = parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1",
        "::1",
        "localhost",
    }
    if not parsed.hostname or not (is_https or is_loopback_http):
        raise SourceReviewError(f"official source URL is not safe to fetch: {value}")


def _same_origin(first: str, second: str) -> bool:
    left = urlparse(first)
    right = urlparse(second)
    return (
        left.scheme.casefold(),
        (left.hostname or "").casefold().rstrip("."),
        left.port,
    ) == (
        right.scheme.casefold(),
        (right.hostname or "").casefold().rstrip("."),
        right.port,
    )


def _require_outside_repository(destination: Path, root: Path) -> None:
    try:
        destination.relative_to(root)
    except ValueError:
        return
    raise SourceReviewError(
        "source-review bundle must be written outside the repository"
    )


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SourceReviewError("retrieved_at_utc must be a UTC timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceReviewError("retrieved_at_utc must be a UTC timestamp") from exc


def _write_private_bytes(destination: Path, payload: bytes) -> None:
    destination.write_bytes(payload)
    destination.chmod(0o600)


def _write_private_json(destination: Path, payload: dict[str, Any]) -> None:
    serialized = (
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
    temporary_path: Path | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.chmod(0o600)
        temporary_path.replace(destination)
        destination.chmod(0o600)
    except OSError as exc:
        raise SourceReviewError(
            f"could not write source-review bundle: {destination}"
        ) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Archive approved candidate official pages into a private bundle "
            "with blank human-review decisions."
        )
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--output", required=True)
    parser.add_argument("--retrieved-at-utc", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the official-source review bundle CLI."""

    args = _argument_parser().parse_args(argv)
    root = Path(args.repo_root).resolve()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = root / config_path
    try:
        write_source_review_bundle(
            repo_root=root,
            config_path=config_path,
            output_dir=args.output,
            retrieved_at_utc=args.retrieved_at_utc,
        )
    except SourceReviewError as exc:
        print(f"source-review bundle error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote source-review bundle to {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
