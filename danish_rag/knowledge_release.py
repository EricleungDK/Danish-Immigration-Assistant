"""Local knowledge-release installation for the first reviewed corpus slice."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BUNDLED_MINIMAL_RELEASE = ROOT / "data" / "knowledge_releases" / "kr-2026-07-06.1"
ACTIVE_RELEASE_FILE = "active-release.json"


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

    resolved_data_dir = Path(data_dir)
    resolved_release_dir = Path(release_dir)
    manifest = _load_manifest(resolved_release_dir)
    release_id = str(manifest["knowledge_release_id"])
    active = _load_active_release_file(resolved_data_dir)
    if active and active.get("manifest", {}).get("knowledge_release_id") == release_id:
        try:
            return {
                "manifest": active["manifest"],
                "documents": load_active_documents(resolved_data_dir),
                "index": _load_index_metadata(resolved_data_dir, release_id),
                "active": active,
            }
        except Exception:
            pass

    documents = _load_release_documents(resolved_release_dir, manifest)
    _validate_release(manifest, documents)

    corpus_dir = resolved_data_dir / "corpus" / release_id
    corpus_dir.mkdir(parents=True, exist_ok=True)
    source_documents_path = resolved_release_dir / "corpus" / "documents.json"
    installed_documents_path = corpus_dir / "documents.json"
    temporary_documents_path = installed_documents_path.with_suffix(".json.tmp")
    shutil.copyfile(source_documents_path, temporary_documents_path)
    temporary_documents_path.replace(installed_documents_path)

    from .retrieval import build_hybrid_index

    index = build_hybrid_index(
        resolved_data_dir,
        documents,
        manifest=manifest,
    )
    active_release = {
        "manifest": manifest,
        "documents_path": str(installed_documents_path),
        "index_path": str(resolved_data_dir / "index" / release_id),
        "installed_at_utc": datetime.now(UTC).isoformat(),
    }
    _write_json_atomic(active_release, resolved_data_dir / ACTIVE_RELEASE_FILE)
    return {
        "manifest": manifest,
        "documents": documents,
        "index": index,
        "active": active_release,
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


def _validate_release(manifest: dict[str, Any], documents: list[dict[str, Any]]) -> None:
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
    source_ids = set()
    for source in manifest["sources"]:
        state = source.get("review_state")
        if state not in {"approved-current", "overdue-policy-usable"}:
            raise KnowledgeReleaseError(
                f"Source {source.get('source_id', '<unknown>')} is not release-eligible."
            )
        if not source.get("reviewers"):
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
