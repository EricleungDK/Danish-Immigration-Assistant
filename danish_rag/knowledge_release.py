"""Local knowledge-release installation for the first reviewed corpus slice."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Protocol

from .embedding_provider import EmbeddingProvider, embedding_provider_id, resolve_embedding_provider
from .github_release_client import (
    ArtifactDownloadApproval,
    DownloadedReleaseArtifact,
    GitHubReleaseAsset,
    GitHubReleaseMetadata,
    MAX_ARTIFACT_BYTES,
)
from .release_trust import ReleaseTrustError, verify_manifest_signature


ROOT = Path(__file__).resolve().parents[1]
BUNDLED_MINIMAL_RELEASE = ROOT / "data" / "knowledge_releases" / "kr-2026-07-06.1"
DEFAULT_RELEASE_CATALOG_DIR = ROOT / "data" / "knowledge_releases"
DEFAULT_TRUST_ROOTS_DIR = ROOT / "config" / "trust_roots"
ACTIVE_RELEASE_FILE = "active-release.json"
PENDING_UPDATE_FILE = "pending-knowledge-update.json"
AVAILABLE_GITHUB_UPDATE_FILE = "available-github-knowledge-update.json"
GITHUB_UPDATE_STAGING_DIR = "knowledge-update-staging"
GITHUB_UPDATE_WORK_DIR = ".knowledge-update-work"
APPLICATION_VERSION = "0.1.0"
MAX_RELEASE_ARCHIVE_MEMBERS = 10_000
MAX_EXPANDED_RELEASE_BYTES = 512 * 1024 * 1024
ARCHIVE_COPY_CHUNK_BYTES = 1024 * 1024
GITHUB_KNOWLEDGE_RELEASE_PATTERN = re.compile(
    r"kr-(\d{4})-(\d{2})-(\d{2})\.(\d+)\Z"
)


class KnowledgeReleaseError(ValueError):
    """Raised when a bundled or installed knowledge release is invalid."""


class GitHubArtifactDownloader(Protocol):
    repository: str

    def download_artifact(
        self,
        asset: GitHubReleaseAsset,
        destination_dir: str | Path,
        *,
        approval: ArtifactDownloadApproval | None,
    ) -> DownloadedReleaseArtifact: ...


def default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    data_home = Path(base) if base else Path.home() / ".local" / "share"
    return data_home / "danish-immigration-rag"


def install_minimal_knowledge_release(
    data_dir: str | Path,
    *,
    release_dir: str | Path = BUNDLED_MINIMAL_RELEASE,
    embedding_model: str | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_endpoint: str | None = None,
    trust_root_path: str | Path | None = None,
    fault_injector: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Install the bundled minimal reviewed release and build its local hybrid index."""

    return install_knowledge_release(
        data_dir,
        release_dir=release_dir,
        embedding_model=embedding_model,
        embedding_provider=embedding_provider,
        embedding_endpoint=embedding_endpoint,
        trust_root_path=trust_root_path,
        fault_injector=fault_injector,
    )


def install_knowledge_release(
    data_dir: str | Path,
    *,
    release_dir: str | Path,
    embedding_model: str | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_endpoint: str | None = None,
    trust_root_path: str | Path | None = None,
    expected_release_id: str | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    fault_injector: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Install a verified reviewed knowledge release and build its local hybrid index."""

    from .retrieval import embedding_model_profile, inspect_embedding_model

    embedding_profile = embedding_model_profile(embedding_model)
    resolved_embedding_provider = resolve_embedding_provider(
        embedding_provider,
        endpoint=embedding_endpoint,
    )
    resolved_data_dir = Path(data_dir)
    resolved_release_dir = Path(release_dir)
    progress = _InstallProgress(progress_callback)
    progress.report(
        "verification",
        "Verifying release manifest, compatibility, and artifact integrity.",
        10,
    )
    _inject_install_fault(fault_injector, "verification")
    verified = verify_knowledge_release(
        resolved_release_dir,
        trust_root_path=trust_root_path,
    )
    manifest = verified["manifest"]
    documents = verified["documents"]
    release_id = str(manifest["knowledge_release_id"])
    if expected_release_id is not None and release_id != expected_release_id:
        raise KnowledgeReleaseError(
            "Signed manifest release ID does not match the approved knowledge update."
        )
    resolved_model_identity = inspect_embedding_model(
        resolved_embedding_provider,
        str(embedding_profile["name"]),
    )
    active = _load_active_release_file(resolved_data_dir)
    if (
        active
        and active.get("manifest", {}).get("knowledge_release_id") == release_id
        and _active_index_matches_embedding_contract(
            resolved_data_dir,
            release_id,
            embedding_model=str(embedding_profile["name"]),
            embedding_provider=resolved_embedding_provider,
            embedding_model_identity=resolved_model_identity,
        )
    ):
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
        embedding_model=str(embedding_profile["name"]),
        embedding_provider=resolved_embedding_provider,
        embedding_model_identity=resolved_model_identity,
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
        embedding_model=str(embedding_profile["name"]),
        embedding_provider=resolved_embedding_provider,
        embedding_model_identity=resolved_model_identity,
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


def ensure_minimal_knowledge_release(
    data_dir: str | Path,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_endpoint: str | None = None,
    trust_root_path: str | Path | None = None,
) -> dict[str, Any]:
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
        return install_minimal_knowledge_release(
            data_dir,
            embedding_provider=embedding_provider,
            embedding_endpoint=embedding_endpoint,
            trust_root_path=trust_root_path,
        )


def load_active_release(data_dir: str | Path) -> dict[str, Any]:
    resolved_data_dir = Path(data_dir)
    active_path = resolved_data_dir / ACTIVE_RELEASE_FILE
    if not active_path.exists():
        raise FileNotFoundError(active_path)
    active_release = json.loads(active_path.read_text(encoding="utf-8"))
    if not isinstance(active_release, dict):
        raise KnowledgeReleaseError("Active release record must be a JSON object.")
    _validate_active_release_pair(resolved_data_dir, active_release)
    return active_release


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
    index = _load_index_metadata(Path(data_dir), str(manifest["knowledge_release_id"]))
    return {
        "knowledge_release_id": str(manifest["knowledge_release_id"]),
        "corpus_id": str(manifest["corpus_id"]),
        "source_registry_version": str(manifest["source_registry_version"]),
        "created_at_utc": str(manifest["created_at_utc"]),
        "embedding_model": str(index["embedding_model"]),
        "embedding_vector_dimensions": str(index["vector_dimensions"]),
        "index_schema_version": str(index["schema_version"]),
    }


def discover_knowledge_update(
    data_dir: str | Path,
    release_catalog_dir: str | Path = DEFAULT_RELEASE_CATALOG_DIR,
    *,
    application_version: str = APPLICATION_VERSION,
    trust_root_path: str | Path | None = None,
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
                trust_root_path=trust_root_path,
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


def select_github_knowledge_update(
    data_dir: str | Path,
    releases: tuple[GitHubReleaseMetadata, ...],
) -> dict[str, Any] | None:
    """Select newer content-free GitHub metadata for the expected release archive."""

    active_release_id = str(
        load_active_release(data_dir)["manifest"]["knowledge_release_id"]
    )
    active_version = _knowledge_release_version(active_release_id)
    candidates: list[dict[str, Any]] = []
    for release in releases:
        try:
            candidate_version = _knowledge_release_version(release.tag_name)
        except KnowledgeReleaseError:
            continue
        if candidate_version <= active_version:
            continue
        expected_asset_name = f"{release.tag_name}.zip"
        expected_assets = [
            asset
            for asset in release.assets
            if asset.name == expected_asset_name
            and asset.release_tag == release.tag_name
            and 0 < asset.size_bytes <= MAX_ARTIFACT_BYTES
        ]
        if len(expected_assets) != 1:
            continue
        asset = expected_assets[0]
        candidates.append(
            {
                "schema_version": "1.0",
                "channel": "github-releases",
                "repository": release.repository,
                "release": {
                    "knowledge_release_id": release.tag_name,
                    "github_release_id": release.github_release_id,
                    "published_at_utc": release.published_at_utc,
                },
                "artifact": {
                    "asset_id": asset.asset_id,
                    "name": asset.name,
                    "content_type": asset.content_type,
                    "size_bytes": asset.size_bytes,
                    "browser_download_url": asset.browser_download_url,
                    "github_sha256": asset.github_sha256,
                },
            }
        )
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda candidate: _knowledge_release_version(
            str(candidate["release"]["knowledge_release_id"])
        ),
    )


def save_available_github_knowledge_update(
    data_dir: str | Path,
    update: dict[str, Any] | None,
) -> None:
    path = Path(data_dir) / AVAILABLE_GITHUB_UPDATE_FILE
    if update is None:
        dismiss_available_github_knowledge_update(data_dir)
        return
    _validate_available_github_update(update)
    _write_json_atomic(update, path)


def load_available_github_knowledge_update(
    data_dir: str | Path,
) -> dict[str, Any] | None:
    path = Path(data_dir) / AVAILABLE_GITHUB_UPDATE_FILE
    if not path.exists():
        return None
    try:
        update = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KnowledgeReleaseError(
            "Available GitHub knowledge update record is invalid."
        ) from exc
    _validate_available_github_update(update)
    return update


def dismiss_available_github_knowledge_update(data_dir: str | Path) -> None:
    path = Path(data_dir) / AVAILABLE_GITHUB_UPDATE_FILE
    path.unlink(missing_ok=True)


def prepare_github_knowledge_update(
    data_dir: str | Path,
    client: GitHubArtifactDownloader,
    *,
    approval: ArtifactDownloadApproval,
    application_version: str = APPLICATION_VERSION,
    trust_root_path: str | Path | None = None,
    max_expanded_bytes: int = MAX_EXPANDED_RELEASE_BYTES,
) -> dict[str, Any]:
    """Download, safely unpack, and verify one explicitly approved GitHub release."""

    resolved_data_dir = Path(data_dir)
    available = load_available_github_knowledge_update(resolved_data_dir)
    if available is None:
        raise KnowledgeReleaseError("No GitHub knowledge update metadata is pending.")
    release = available["release"]
    artifact_record = available["artifact"]
    release_id = str(release["knowledge_release_id"])
    if (
        not isinstance(approval, ArtifactDownloadApproval)
        or not approval.approved
        or approval.requested_knowledge_release_id != release_id
        or approval.artifact_id != artifact_record["asset_id"]
        or approval.artifact_name != artifact_record["name"]
    ):
        raise KnowledgeReleaseError(
            "Release artifact approval does not match the available GitHub update."
        )
    if available["repository"] != client.repository:
        raise KnowledgeReleaseError(
            "Available knowledge update belongs to a different GitHub repository."
        )
    asset = GitHubReleaseAsset(
        repository=str(available["repository"]),
        release_tag=release_id,
        asset_id=int(artifact_record["asset_id"]),
        name=str(artifact_record["name"]),
        content_type=str(artifact_record["content_type"]),
        size_bytes=int(artifact_record["size_bytes"]),
        browser_download_url=str(artifact_record["browser_download_url"]),
        github_sha256=artifact_record["github_sha256"],
    )

    work_dir = (
        resolved_data_dir / GITHUB_UPDATE_WORK_DIR / f"{release_id}-{uuid.uuid4().hex}"
    )
    download_dir = work_dir / "download"
    extracted_dir = work_dir / "extracted"
    final_release_dir = (
        resolved_data_dir / GITHUB_UPDATE_STAGING_DIR / release_id
    )
    if final_release_dir.exists():
        raise KnowledgeReleaseError(
            "This GitHub knowledge release is already prepared; dismiss it before retrying."
        )
    try:
        downloaded = client.download_artifact(
            asset,
            download_dir,
            approval=approval,
        )
        release_dir = _extract_release_archive(
            downloaded.path,
            extracted_dir,
            expected_release_id=release_id,
            max_expanded_bytes=max_expanded_bytes,
        )
        verified = verify_knowledge_release(
            release_dir,
            application_version=application_version,
            trust_root_path=trust_root_path,
        )
        manifest = verified["manifest"]
        if manifest["knowledge_release_id"] != release_id:
            raise KnowledgeReleaseError(
                "Signed manifest release ID does not match the approved GitHub tag."
            )
        active_manifest = load_active_release(resolved_data_dir)["manifest"]
        summary = _update_summary(
            active_manifest=active_manifest,
            manifest=manifest,
            documents=verified["documents"],
            application_version=application_version,
        )
        summary["distribution"] = {
            "channel": "github-releases",
            "repository": available["repository"],
            "github_release_id": release["github_release_id"],
            "release_tag": release_id,
            "asset_id": artifact_record["asset_id"],
            "asset_name": artifact_record["name"],
            "downloaded_bytes": downloaded.bytes_written,
            "downloaded_sha256": downloaded.sha256,
            "github_digest_verified": downloaded.github_digest_verified,
        }
        summary["signed_manifest"] = {
            "verified": True,
            "signature_algorithm": manifest["integrity"]["signature_algorithm"],
            "trust_root_id": manifest["integrity"]["trust_root_id"],
        }
        final_release_dir.parent.mkdir(parents=True, exist_ok=True)
        release_dir.replace(final_release_dir)
        save_pending_knowledge_update(resolved_data_dir, summary)
        dismiss_available_github_knowledge_update(resolved_data_dir)
        return summary
    except KnowledgeReleaseError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as exc:
        raise KnowledgeReleaseError(
            "GitHub knowledge release could not be safely prepared."
        ) from exc
    finally:
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)


def _validate_available_github_update(update: Any) -> None:
    if not isinstance(update, dict):
        raise KnowledgeReleaseError("GitHub knowledge update metadata must be an object.")
    if update.get("schema_version") != "1.0" or update.get("channel") != "github-releases":
        raise KnowledgeReleaseError("GitHub knowledge update metadata has an invalid schema.")
    repository = update.get("repository")
    release = update.get("release")
    artifact = update.get("artifact")
    if not isinstance(repository, str) or not repository:
        raise KnowledgeReleaseError("GitHub knowledge update repository is invalid.")
    if not isinstance(release, dict) or not isinstance(artifact, dict):
        raise KnowledgeReleaseError("GitHub knowledge update metadata is incomplete.")
    release_id = release.get("knowledge_release_id")
    if not isinstance(release_id, str):
        raise KnowledgeReleaseError("GitHub knowledge release ID is invalid.")
    _knowledge_release_version(release_id)
    if (
        isinstance(release.get("github_release_id"), bool)
        or not isinstance(release.get("github_release_id"), int)
        or release["github_release_id"] <= 0
    ):
        raise KnowledgeReleaseError("GitHub release ID is invalid.")
    asset_id = artifact.get("asset_id")
    size_bytes = artifact.get("size_bytes")
    if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id <= 0:
        raise KnowledgeReleaseError("GitHub knowledge release asset ID is invalid.")
    if (
        isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or not 0 < size_bytes <= MAX_ARTIFACT_BYTES
    ):
        raise KnowledgeReleaseError("GitHub knowledge release asset size is invalid.")
    if artifact.get("name") != f"{release_id}.zip":
        raise KnowledgeReleaseError("GitHub knowledge release archive name is invalid.")
    for field in ("content_type", "browser_download_url"):
        if not isinstance(artifact.get(field), str) or not artifact[field]:
            raise KnowledgeReleaseError(
                f"GitHub knowledge release asset {field} is invalid."
            )
    digest = artifact.get("github_sha256")
    if digest is not None and (
        not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise KnowledgeReleaseError("GitHub knowledge release asset digest is invalid.")


def _extract_release_archive(
    archive_path: Path,
    destination: Path,
    *,
    expected_release_id: str,
    max_expanded_bytes: int,
) -> Path:
    if (
        isinstance(max_expanded_bytes, bool)
        or not isinstance(max_expanded_bytes, int)
        or not 0 < max_expanded_bytes <= MAX_EXPANDED_RELEASE_BYTES
    ):
        raise KnowledgeReleaseError("Expanded release size limit is invalid.")
    destination.mkdir(parents=True, exist_ok=False)
    destination_root = destination.resolve()
    total_written = 0
    seen_paths: set[PurePosixPath] = set()
    with zipfile.ZipFile(archive_path, "r") as archive:
        members = archive.infolist()
        if not members or len(members) > MAX_RELEASE_ARCHIVE_MEMBERS:
            raise KnowledgeReleaseError("Knowledge release archive member count is invalid.")
        declared_total = sum(member.file_size for member in members)
        if declared_total > max_expanded_bytes:
            raise KnowledgeReleaseError(
                "Knowledge release archive exceeds the expanded-size limit."
            )
        for member in members:
            relative_path = _safe_archive_member_path(member)
            if relative_path in seen_paths:
                raise KnowledgeReleaseError(
                    "Knowledge release archive contains duplicate paths."
                )
            seen_paths.add(relative_path)
            target = destination.joinpath(*relative_path.parts)
            resolved_target = target.resolve()
            if destination_root not in resolved_target.parents:
                raise KnowledgeReleaseError(
                    "Knowledge release archive contains an unsafe path."
                )
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=False)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            member_written = 0
            with archive.open(member, "r") as source, target.open("xb") as output:
                while True:
                    chunk = source.read(ARCHIVE_COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    member_written += len(chunk)
                    total_written += len(chunk)
                    if total_written > max_expanded_bytes:
                        raise KnowledgeReleaseError(
                            "Knowledge release archive exceeds the expanded-size limit."
                        )
                    output.write(chunk)
            if member_written != member.file_size:
                raise KnowledgeReleaseError(
                    "Knowledge release archive member size is inconsistent."
                )
    direct_manifest = destination / "manifest.json"
    if direct_manifest.is_file():
        return destination
    children = list(destination.iterdir())
    if (
        len(children) == 1
        and children[0].is_dir()
        and children[0].name == expected_release_id
        and (children[0] / "manifest.json").is_file()
    ):
        return children[0]
    raise KnowledgeReleaseError(
        "Knowledge release archive does not contain the expected release layout."
    )


def _safe_archive_member_path(member: zipfile.ZipInfo) -> PurePosixPath:
    name = member.filename
    if not name or "\x00" in name or "\\" in name:
        raise KnowledgeReleaseError("Knowledge release archive contains an unsafe path.")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise KnowledgeReleaseError("Knowledge release archive contains an unsafe path.")
    if ":" in path.parts[0]:
        raise KnowledgeReleaseError("Knowledge release archive contains an unsafe path.")
    if member.flag_bits & 0x1:
        raise KnowledgeReleaseError("Encrypted knowledge release archives are unsupported.")
    unix_mode = (member.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    if file_type == stat.S_IFLNK:
        raise KnowledgeReleaseError("Knowledge release archive contains a symbolic link.")
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise KnowledgeReleaseError("Knowledge release archive contains a special file.")
    if member.is_dir() and file_type == stat.S_IFREG:
        raise KnowledgeReleaseError("Knowledge release archive member type is inconsistent.")
    return path


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
    resolved_data_dir = Path(data_dir)
    path = resolved_data_dir / PENDING_UPDATE_FILE
    try:
        pending = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        pending = None
    if path.exists():
        path.unlink()
    if (
        isinstance(pending, dict)
        and isinstance(pending.get("distribution"), dict)
        and pending["distribution"].get("channel") == "github-releases"
    ):
        release_id = pending.get("release", {}).get("knowledge_release_id")
        if isinstance(release_id, str) and GITHUB_KNOWLEDGE_RELEASE_PATTERN.fullmatch(
            release_id
        ):
            staged = prepared_github_knowledge_release_dir(
                resolved_data_dir,
                release_id,
            )
            if staged.exists():
                shutil.rmtree(staged)


def prepared_github_knowledge_release_dir(
    data_dir: str | Path,
    release_id: str,
) -> Path:
    _knowledge_release_version(release_id)
    return Path(data_dir) / GITHUB_UPDATE_STAGING_DIR / release_id


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
    trust_root_path: str | Path | None = None,
) -> dict[str, Any]:
    """Verify a publishable knowledge-release directory against the release contract."""

    resolved_release_dir = Path(release_dir)
    manifest = _load_manifest(resolved_release_dir)
    _validate_integrity_contract(manifest)
    _verify_release_signature(
        resolved_release_dir,
        manifest,
        trust_root_path=trust_root_path,
    )
    documents = _load_release_documents(resolved_release_dir, manifest)
    _validate_release(manifest, documents, application_version=application_version)
    return {"manifest": manifest, "documents": documents}


def _verify_release_signature(
    release_dir: Path,
    manifest: dict[str, Any],
    *,
    trust_root_path: str | Path | None,
) -> None:
    integrity = manifest["integrity"]
    signature_reference = str(integrity["signature"])
    signature_path = _safe_release_path(
        release_dir,
        signature_reference,
        label="detached signature",
    )
    trust_root_id = str(integrity["trust_root_id"])
    resolved_trust_root = (
        Path(trust_root_path)
        if trust_root_path is not None
        else DEFAULT_TRUST_ROOTS_DIR / f"{trust_root_id}.json"
    )
    try:
        verify_manifest_signature(
            release_dir / "manifest.json",
            signature_path,
            resolved_trust_root,
            trust_root_id,
        )
    except ReleaseTrustError as exc:
        raise KnowledgeReleaseError(
            f"Knowledge release signature verification failed: {exc}"
        ) from exc


def _validate_integrity_contract(manifest: dict[str, Any]) -> None:
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
    if integrity["signature_algorithm"] != "ed25519":
        raise KnowledgeReleaseError("Unsupported release signature algorithm.")
    if integrity["signature"] != "manifest.sig":
        raise KnowledgeReleaseError(
            "Release manifest must reference the detached manifest.sig signature."
        )


def _safe_release_path(release_dir: Path, reference: str, *, label: str) -> Path:
    relative = Path(reference)
    if not reference or relative.is_absolute() or ".." in relative.parts:
        raise KnowledgeReleaseError(f"Release {label} path is unsafe.")
    resolved_release = release_dir.resolve()
    resolved_path = (release_dir / relative).resolve()
    if resolved_release not in resolved_path.parents:
        raise KnowledgeReleaseError(f"Release {label} path is unsafe.")
    return resolved_path


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
    _validate_integrity_contract(manifest)
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


def _validate_active_release_pair(data_dir: Path, active_release: dict[str, Any]) -> None:
    manifest = active_release.get("manifest")
    if not isinstance(manifest, dict):
        raise KnowledgeReleaseError(
            "Installed active corpus/index pair is incomplete: missing manifest."
        )
    release_id = str(manifest.get("knowledge_release_id", ""))
    corpus_id = str(manifest.get("corpus_id", ""))
    if not release_id or not corpus_id:
        raise KnowledgeReleaseError(
            "Installed active corpus/index pair is incomplete: missing release identity."
        )

    expected_documents_path = data_dir / "corpus" / release_id / "documents.json"
    expected_index_path = data_dir / "index" / release_id
    documents_path = Path(str(active_release.get("documents_path", "")))
    index_path = Path(str(active_release.get("index_path", "")))
    if _normalized_path(documents_path) != _normalized_path(expected_documents_path):
        raise KnowledgeReleaseError(
            "Installed active corpus/index pair is mismatched: documents path does not "
            f"match active release {release_id}."
        )
    if _normalized_path(index_path) != _normalized_path(expected_index_path):
        raise KnowledgeReleaseError(
            "Installed active corpus/index pair is mismatched: index path does not "
            f"match active release {release_id}."
        )
    if not documents_path.exists():
        raise KnowledgeReleaseError(
            "Installed active corpus/index pair is incomplete: corpus documents are missing."
        )
    if not index_path.exists():
        raise KnowledgeReleaseError(
            "Installed active corpus/index pair is incomplete: retrieval index is missing."
        )

    documents = json.loads(documents_path.read_text(encoding="utf-8"))
    if not isinstance(documents, list):
        raise KnowledgeReleaseError(
            "Installed active corpus/index pair is invalid: corpus documents must be a JSON array."
        )

    metadata_path = index_path / "index-metadata.json"
    dense_index_path = index_path / "dense-index.json"
    lexical_index_path = index_path / "lexical.sqlite3"
    for path, label in (
        (metadata_path, "index metadata"),
        (dense_index_path, "dense index"),
        (lexical_index_path, "lexical index"),
    ):
        if not path.exists():
            raise KnowledgeReleaseError(
                f"Installed active corpus/index pair is incomplete: {label} is missing."
            )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    dense_index = json.loads(dense_index_path.read_text(encoding="utf-8"))
    dense_metadata = dense_index.get("metadata") if isinstance(dense_index, dict) else None
    if not isinstance(metadata, dict) or not isinstance(dense_metadata, dict):
        raise KnowledgeReleaseError(
            "Installed active corpus/index pair is invalid: index metadata is malformed."
        )
    for field, expected in {
        "knowledge_release_id": release_id,
        "corpus_identity": corpus_id,
    }.items():
        if metadata.get(field) != expected or dense_metadata.get(field) != expected:
            raise KnowledgeReleaseError(
                "Installed active corpus/index pair is mismatched: index metadata does "
                f"not match active release {release_id}."
            )


def _normalized_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _load_index_metadata(data_dir: Path, release_id: str) -> dict[str, Any]:
    metadata_path = data_dir / "index" / release_id / "index-metadata.json"
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def _active_index_matches_embedding_contract(
    data_dir: Path,
    release_id: str,
    *,
    embedding_model: str,
    embedding_provider: EmbeddingProvider,
    embedding_model_identity: dict[str, Any],
) -> bool:
    try:
        index = _load_index_metadata(data_dir, release_id)
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return False
    return (
        index.get("embedding_model") == embedding_model
        and index.get("embedding_provider") == embedding_provider_id(embedding_provider)
        and index.get("embedding_model_identity") == embedding_model_identity
    )


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
    temporary_path = path.with_name(
        f".{path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary_path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


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


def _knowledge_release_version(release_id: str) -> tuple[int, int, int, int]:
    match = GITHUB_KNOWLEDGE_RELEASE_PATTERN.fullmatch(release_id)
    if match is None:
        raise KnowledgeReleaseError("Knowledge release ID has an unsafe format.")
    year, month, day, sequence = (int(part) for part in match.groups())
    return year, month, day, sequence
