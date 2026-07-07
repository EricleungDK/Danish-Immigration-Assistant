"""Local knowledge-release installation for the first reviewed corpus slice."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
BUNDLED_MINIMAL_RELEASE = ROOT / "data" / "knowledge_releases" / "kr-2026-07-06.1"
DEFAULT_RELEASE_CATALOG_DIR = ROOT / "data" / "knowledge_releases"
ACTIVE_RELEASE_FILE = "active-release.json"
PENDING_UPDATE_FILE = "pending-knowledge-update.json"
APPLICATION_VERSION = "0.1.0"


class KnowledgeReleaseError(ValueError):
    """Raised when a bundled or installed knowledge release is invalid."""


def default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    data_home = Path(base) if base else Path.home() / ".local" / "share"
    return data_home / "danish-immigration-rag"


def install_minimal_knowledge_release(
    data_dir: str | Path,
    *,
    release_dir: str | Path = BUNDLED_MINIMAL_RELEASE,
) -> dict[str, Any]:
    """Install the bundled minimal reviewed release and build its local hybrid index."""

    return install_knowledge_release(data_dir, release_dir=release_dir)


def install_knowledge_release(
    data_dir: str | Path,
    *,
    release_dir: str | Path,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    fault_injector: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Install a verified reviewed knowledge release and build its local hybrid index."""

    resolved_data_dir = Path(data_dir)
    resolved_release_dir = Path(release_dir)
    progress = _InstallProgress(progress_callback)
    progress.report(
        "verification",
        "Verifying release manifest, compatibility, and artifact integrity.",
        10,
    )
    _inject_install_fault(fault_injector, "verification")
    verified = verify_knowledge_release(resolved_release_dir)
    manifest = verified["manifest"]
    documents = verified["documents"]
    release_id = str(manifest["knowledge_release_id"])
    active = _load_active_release_file(resolved_data_dir)
    if active and active.get("manifest", {}).get("knowledge_release_id") == release_id:
        try:
            progress.report("already_active", "Knowledge release is already active.", 100)
            return {
                "manifest": active["manifest"],
                "documents": load_active_documents(resolved_data_dir),
                "index": _load_index_metadata(resolved_data_dir, release_id),
                "active": active,
                "progress": progress.entries,
            }
        except Exception:
            pass

    staging_dir = _staging_dir(resolved_data_dir, release_id)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    progress.report("extraction", "Preparing reviewed corpus artifact locally.", 30)
    _inject_install_fault(fault_injector, "extraction")
    source_documents_path = resolved_release_dir / "corpus" / "documents.json"
    staging_corpus_dir = staging_dir / "corpus" / release_id
    staging_corpus_dir.mkdir(parents=True, exist_ok=True)
    installed_documents_path = staging_corpus_dir / "documents.json"
    temporary_documents_path = installed_documents_path.with_suffix(".json.tmp")
    shutil.copyfile(source_documents_path, temporary_documents_path)
    temporary_documents_path.replace(installed_documents_path)

    from .retrieval import build_hybrid_index
    from .retrieval import HybridRetriever

    index = build_hybrid_index(
        staging_dir,
        documents,
        manifest=manifest,
        progress_callback=progress.report_from_event,
        fault_injector=fault_injector,
    )
    progress.report(
        "compatibility",
        "Checking staged corpus and index compatibility.",
        85,
    )
    _inject_install_fault(fault_injector, "compatibility")
    staged_active_release = {
        "manifest": manifest,
        "documents_path": str(installed_documents_path),
        "index_path": str(staging_dir / "index" / release_id),
        "installed_at_utc": datetime.now(UTC).isoformat(),
    }
    dense_index = json.loads(
        (staging_dir / "index" / release_id / "dense-index.json").read_text(
            encoding="utf-8"
        )
    )
    HybridRetriever(
        data_dir=staging_dir,
        active_release=staged_active_release,
        documents=documents,
        dense_index=dense_index,
    )

    progress.report("activation", "Activating verified corpus and local index.", 95)
    _inject_install_fault(fault_injector, "activation")
    final_documents_path, final_index_path = _promote_staged_release(
        resolved_data_dir,
        staging_dir,
        release_id,
        fault_injector=fault_injector,
    )
    _inject_install_fault(fault_injector, "activation")
    active_release = {
        "manifest": manifest,
        "documents_path": str(final_documents_path),
        "index_path": str(final_index_path),
        "installed_at_utc": datetime.now(UTC).isoformat(),
    }
    _write_json_atomic(active_release, resolved_data_dir / ACTIVE_RELEASE_FILE)
    progress.report("complete", "Knowledge release installation is active.", 100)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    return {
        "manifest": manifest,
        "documents": documents,
        "index": index,
        "active": active_release,
        "progress": progress.entries,
    }


def ensure_minimal_knowledge_release(data_dir: str | Path) -> dict[str, Any]:
    try:
        active = load_active_release(data_dir)
        return {
            "manifest": active["manifest"],
            "documents": load_active_documents(data_dir),
            "index": _load_index_metadata(
                Path(data_dir),
                active["manifest"]["knowledge_release_id"],
            ),
            "active": active,
        }
    except FileNotFoundError:
        return install_minimal_knowledge_release(data_dir)


def load_active_release(data_dir: str | Path) -> dict[str, Any]:
    active_path = Path(data_dir) / ACTIVE_RELEASE_FILE
    if not active_path.exists():
        raise FileNotFoundError(active_path)
    return json.loads(active_path.read_text(encoding="utf-8"))


def load_active_documents(data_dir: str | Path) -> list[dict[str, Any]]:
    active = load_active_release(data_dir)
    documents_path = Path(active["documents_path"])
    documents = json.loads(documents_path.read_text(encoding="utf-8"))
    if not isinstance(documents, list):
        raise KnowledgeReleaseError("Installed corpus documents must be a JSON array.")
    return [dict(document) for document in documents]


def active_corpus_summary(data_dir: str | Path) -> dict[str, str]:
    active = load_active_release(data_dir)
    manifest = active["manifest"]
    return {
        "knowledge_release_id": str(manifest["knowledge_release_id"]),
        "corpus_id": str(manifest["corpus_id"]),
        "source_registry_version": str(manifest["source_registry_version"]),
        "created_at_utc": str(manifest["created_at_utc"]),
    }


def discover_knowledge_update(
    data_dir: str | Path,
    release_catalog_dir: str | Path = DEFAULT_RELEASE_CATALOG_DIR,
    *,
    application_version: str = APPLICATION_VERSION,
) -> dict[str, Any] | None:
    """Find the newest compatible reviewed release without installing artifacts."""

    active = load_active_release(data_dir)
    active_manifest = active["manifest"]
    active_release_id = str(active_manifest["knowledge_release_id"])
    active_created_at = str(active_manifest["created_at_utc"])
    candidates: list[dict[str, Any]] = []
    catalog = Path(release_catalog_dir)
    if not catalog.exists():
        return None

    for release_dir in sorted(path for path in catalog.iterdir() if path.is_dir()):
        try:
            verified = verify_knowledge_release(
                release_dir,
                application_version=application_version,
            )
        except (KnowledgeReleaseError, OSError, json.JSONDecodeError):
            continue
        manifest = verified["manifest"]
        release_id = str(manifest["knowledge_release_id"])
        created_at = str(manifest["created_at_utc"])
        if release_id == active_release_id:
            continue
        if (created_at, release_id) <= (active_created_at, active_release_id):
            continue
        candidates.append(
            _update_summary(
                active_manifest=active_manifest,
                manifest=manifest,
                documents=verified["documents"],
                application_version=application_version,
            )
        )

    if not candidates:
        return None
    return max(
        candidates,
        key=lambda update: (
            update["release"]["created_at_utc"],
            update["release"]["knowledge_release_id"],
        ),
    )


def save_pending_knowledge_update(
    data_dir: str | Path,
    update: dict[str, Any] | None,
) -> None:
    path = Path(data_dir) / PENDING_UPDATE_FILE
    if update is None:
        dismiss_pending_knowledge_update(data_dir)
        return
    _write_json_atomic(update, path)


def load_pending_knowledge_update(data_dir: str | Path) -> dict[str, Any] | None:
    path = Path(data_dir) / PENDING_UPDATE_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def dismiss_pending_knowledge_update(data_dir: str | Path) -> None:
    path = Path(data_dir) / PENDING_UPDATE_FILE
    if path.exists():
        path.unlink()


def _update_summary(
    *,
    active_manifest: dict[str, Any],
    manifest: dict[str, Any],
    documents: list[dict[str, Any]],
    application_version: str,
) -> dict[str, Any]:
    active_sources = {
        str(source["source_id"]): source for source in active_manifest.get("sources", [])
    }
    candidate_sources = {
        str(source["source_id"]): source for source in manifest.get("sources", [])
    }
    added = sorted(set(candidate_sources) - set(active_sources))
    removed = sorted(set(active_sources) - set(candidate_sources))
    updated = sorted(
        source_id
        for source_id in set(active_sources) & set(candidate_sources)
        if _source_change_fingerprint(active_sources[source_id])
        != _source_change_fingerprint(candidate_sources[source_id])
    )
    artifact = _documents_artifact(manifest)
    return {
        "release": {
            "knowledge_release_id": str(manifest["knowledge_release_id"]),
            "corpus_id": str(manifest["corpus_id"]),
            "source_registry_version": str(manifest["source_registry_version"]),
            "created_at_utc": str(manifest["created_at_utc"]),
        },
        "compatibility": {
            "status": "compatible",
            "minimum_application_version": str(manifest["minimum_application_version"]),
            "application_version": application_version,
        },
        "reviewed_source_changes": {
            "added": len(added),
            "updated": len(updated),
            "removed": len(removed),
            "added_sources": [_source_summary(candidate_sources[source_id]) for source_id in added],
            "updated_sources": [
                _source_summary(candidate_sources[source_id]) for source_id in updated
            ],
            "removed_sources": [_source_summary(active_sources[source_id]) for source_id in removed],
        },
        "expected_local_indexing_work": {
            "document_count": len(documents),
            "artifact_bytes": int(artifact["bytes"]),
            "work": "copy reviewed corpus artifact and rebuild local hybrid index",
        },
    }


def _source_change_fingerprint(source: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(source.get("source_content_sha256", "")),
        str(source.get("normalized_document_sha256", "")),
        str(source.get("reviewed_at_utc", "")),
    )


def _source_summary(source: dict[str, Any]) -> dict[str, str]:
    return {
        "source_id": str(source["source_id"]),
        "title": str(source.get("title", source["source_id"])),
        "publisher": str(source.get("publisher", "")),
        "topic": str(source.get("topic", "")),
    }


def verify_knowledge_release(
    release_dir: str | Path,
    *,
    application_version: str = APPLICATION_VERSION,
) -> dict[str, Any]:
    """Verify a publishable knowledge-release directory against the release contract."""

    resolved_release_dir = Path(release_dir)
    manifest = _load_manifest(resolved_release_dir)
    documents = _load_release_documents(resolved_release_dir, manifest)
    _validate_release(manifest, documents, application_version=application_version)
    return {"manifest": manifest, "documents": documents}


def _load_manifest(release_dir: Path) -> dict[str, Any]:
    manifest_path = release_dir / "manifest.json"
    if not manifest_path.exists():
        raise KnowledgeReleaseError(f"Missing release manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise KnowledgeReleaseError("Release manifest must be a JSON object.")
    return manifest


def _load_release_documents(
    release_dir: Path,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    artifact = _documents_artifact(manifest)
    documents_path = release_dir / artifact["path"]
    if not documents_path.exists():
        raise KnowledgeReleaseError(f"Missing corpus artifact: {documents_path}")
    if _sha256_file(documents_path) != artifact["sha256"]:
        raise KnowledgeReleaseError("Corpus documents hash does not match the manifest.")
    if documents_path.stat().st_size != int(artifact["bytes"]):
        raise KnowledgeReleaseError("Corpus documents byte count does not match the manifest.")
    documents = json.loads(documents_path.read_text(encoding="utf-8"))
    if not isinstance(documents, list):
        raise KnowledgeReleaseError("Corpus documents must be a JSON array.")
    return [dict(document) for document in documents]


def _validate_release(
    manifest: dict[str, Any],
    documents: list[dict[str, Any]],
    *,
    application_version: str = APPLICATION_VERSION,
) -> None:
    required_manifest_fields = {
        "manifest_schema_version",
        "knowledge_release_id",
        "created_at_utc",
        "minimum_application_version",
        "corpus_schema_version",
        "corpus_id",
        "source_registry_version",
        "sources",
        "artifacts",
        "integrity",
    }
    missing = sorted(required_manifest_fields - set(manifest))
    if missing:
        raise KnowledgeReleaseError(f"Release manifest missing field(s): {', '.join(missing)}")
    if manifest["manifest_schema_version"] != "1.0":
        raise KnowledgeReleaseError("Unsupported manifest schema version.")
    if _version_tuple(manifest["minimum_application_version"]) > _version_tuple(
        application_version
    ):
        raise KnowledgeReleaseError(
            "Knowledge release requires application "
            f"{manifest['minimum_application_version']} or newer."
        )
    integrity = manifest.get("integrity")
    if not isinstance(integrity, dict):
        raise KnowledgeReleaseError("Release manifest missing integrity evidence.")
    for field in {
        "hash_algorithm",
        "signature_algorithm",
        "signature",
        "trust_root_id",
    }:
        if not integrity.get(field):
            raise KnowledgeReleaseError(
                f"Release manifest missing integrity evidence: {field}."
            )
    if integrity["hash_algorithm"] != "sha256":
        raise KnowledgeReleaseError("Unsupported integrity hash algorithm.")
    source_ids = set()
    for source in manifest["sources"]:
        provenance_fields = {
            "source_id",
            "publisher",
            "official_url",
            "final_url",
            "topic",
            "language",
            "last_checked_at_utc",
            "source_content_sha256",
            "normalized_document_sha256",
            "extraction_schema_version",
        }
        missing_provenance = sorted(provenance_fields - set(source))
        if missing_provenance:
            raise KnowledgeReleaseError(
                f"Source {source.get('source_id', '<unknown>')} missing provenance "
                f"field(s): {', '.join(missing_provenance)}"
            )
        state = source.get("review_state")
        if state not in {"approved-current", "overdue-policy-usable"}:
            raise KnowledgeReleaseError(
                f"Source {source.get('source_id', '<unknown>')} is not release-eligible."
            )
        if not source.get("reviewers"):
            raise KnowledgeReleaseError(
                f"Source {source.get('source_id', '<unknown>')} lacks human reviewer evidence."
            )
        if not source.get("reviewed_at_utc"):
            raise KnowledgeReleaseError(
                f"Source {source.get('source_id', '<unknown>')} lacks human reviewer evidence."
            )
        source_ids.add(str(source["source_id"]))

    for document in documents:
        required_document_fields = {
            "document_id",
            "source_id",
            "title",
            "publisher",
            "official_url",
            "language",
            "topic_tags",
            "review_state",
            "source_health",
            "checked_at_utc",
            "content",
        }
        missing_document_fields = sorted(required_document_fields - set(document))
        if missing_document_fields:
            raise KnowledgeReleaseError(
                f"Document {document.get('document_id', '<unknown>')} missing field(s): "
                f"{', '.join(missing_document_fields)}"
            )
        if document["source_id"] not in source_ids:
            raise KnowledgeReleaseError(
                f"Document {document['document_id']} references a source not in the manifest."
            )
        if document["review_state"] not in {"approved-current", "overdue-policy-usable"}:
            raise KnowledgeReleaseError(
                f"Document {document['document_id']} is not approved for answer support."
            )
        if document.get("approval_state") != "approved":
            raise KnowledgeReleaseError(
                f"Document {document['document_id']} is missing approval evidence."
            )
        for provenance_field in {"official_url", "final_url", "publisher"}:
            if not document.get(provenance_field):
                raise KnowledgeReleaseError(
                    f"Document {document['document_id']} missing provenance "
                    f"field: {provenance_field}."
                )


def _documents_artifact(manifest: dict[str, Any]) -> dict[str, Any]:
    for artifact in manifest.get("artifacts", []):
        if artifact.get("path") == "corpus/documents.json":
            return dict(artifact)
    raise KnowledgeReleaseError("Release manifest does not list corpus/documents.json.")


def _load_active_release_file(data_dir: Path) -> dict[str, Any] | None:
    active_path = data_dir / ACTIVE_RELEASE_FILE
    if not active_path.exists():
        return None
    return json.loads(active_path.read_text(encoding="utf-8"))


def _load_index_metadata(data_dir: Path, release_id: str) -> dict[str, Any]:
    metadata_path = data_dir / "index" / release_id / "index-metadata.json"
    return json.loads(metadata_path.read_text(encoding="utf-8"))


class _InstallProgress:
    def __init__(
        self,
        callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self.callback = callback
        self.entries: list[dict[str, Any]] = []

    def report(self, phase: str, message: str, percent: int) -> None:
        event = {"phase": phase, "message": message, "percent": percent}
        self.entries.append(event)
        if self.callback is not None:
            self.callback(dict(event))

    def report_from_event(self, event: dict[str, Any]) -> None:
        self.report(
            str(event["phase"]),
            str(event["message"]),
            int(event["percent"]),
        )


def _inject_install_fault(
    fault_injector: Callable[[str], None] | None,
    phase: str,
) -> None:
    if fault_injector is not None:
        fault_injector(phase)


def _staging_dir(data_dir: Path, release_id: str) -> Path:
    return data_dir / ".installing" / f"{release_id}-{uuid.uuid4().hex}"


def _promote_staged_release(
    data_dir: Path,
    staging_dir: Path,
    release_id: str,
    *,
    fault_injector: Callable[[str], None] | None,
) -> tuple[Path, Path]:
    final_corpus_dir = data_dir / "corpus" / release_id
    final_index_dir = data_dir / "index" / release_id
    staged_corpus_dir = staging_dir / "corpus" / release_id
    staged_index_dir = staging_dir / "index" / release_id
    pending_corpus_dir = data_dir / "corpus" / f".{release_id}.pending"
    pending_index_dir = data_dir / "index" / f".{release_id}.pending"
    backup_corpus_dir = data_dir / "corpus" / f".{release_id}.backup"
    backup_index_dir = data_dir / "index" / f".{release_id}.backup"

    for temporary_dir in (
        pending_corpus_dir,
        pending_index_dir,
        backup_corpus_dir,
        backup_index_dir,
    ):
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir)

    pending_corpus_dir.parent.mkdir(parents=True, exist_ok=True)
    pending_index_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staged_corpus_dir, pending_corpus_dir)
    shutil.copytree(staged_index_dir, pending_index_dir)
    _inject_install_fault(fault_injector, "activation")

    try:
        if final_corpus_dir.exists():
            final_corpus_dir.replace(backup_corpus_dir)
        if final_index_dir.exists():
            final_index_dir.replace(backup_index_dir)
        _inject_install_fault(fault_injector, "activation")

        pending_corpus_dir.replace(final_corpus_dir)
        _inject_install_fault(fault_injector, "activation")
        pending_index_dir.replace(final_index_dir)
        _inject_install_fault(fault_injector, "activation")
    except Exception:
        _restore_directory_backup(final_corpus_dir, backup_corpus_dir)
        _restore_directory_backup(final_index_dir, backup_index_dir)
        raise
    finally:
        for temporary_dir in (
            pending_corpus_dir,
            pending_index_dir,
            backup_corpus_dir,
            backup_index_dir,
        ):
            if temporary_dir.exists():
                shutil.rmtree(temporary_dir)
    return final_corpus_dir / "documents.json", final_index_dir


def _restore_directory_backup(final_dir: Path, backup_dir: Path) -> None:
    if final_dir.exists():
        shutil.rmtree(final_dir)
    if backup_dir.exists():
        backup_dir.replace(final_dir)


def _write_json_atomic(value: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in str(version).split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts or [0])
