"""Browser-test server for the local application setup flow."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

import uvicorn

from danish_rag.github_release_client import (
    ArtifactDownloadApproval,
    DownloadedReleaseArtifact,
    GitHubReleaseAsset,
    GitHubReleaseMetadata,
)
from danish_rag.knowledge_release import (
    BUNDLED_MINIMAL_RELEASE,
    dismiss_available_github_knowledge_update,
    dismiss_pending_knowledge_update,
    install_minimal_knowledge_release,
)
from danish_rag.local_app import create_app
from danish_rag.provider_setup import ProviderConfiguration
from danish_rag.source_maintenance import build_publishable_knowledge_release
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture
from tests.release_trust_fixture import create_test_release_trust_fixture


def fixture_capability_tester(configuration: ProviderConfiguration) -> dict[str, Any]:
    if configuration.model == "fail-model":
        return {
            "ok": False,
            "reason": "service_unreachable",
            "message": "Provider service is unreachable. Start the local server and retry.",
        }
    return {
        "ok": True,
        "reason": "passed",
        "message": "Provider capability test passed.",
        "provider_version": "browser-fixture",
        "model_identity": {"id": configuration.model},
        "capabilities": ["generation"],
    }


class FixtureAnswerGenerator:
    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        citation_id = evidence[0]["citation_id"]
        sections = [
            {
                "kind": "official_fact",
                "text": (
                    "Permanent opholdstilladelse can require documentation for "
                    "bestået Prøve i Dansk 2 or an equivalent Danish test."
                ),
                "citation_ids": [citation_id],
            },
            {
                "kind": "interpretation",
                "text": (
                    "Use Prøve i Dansk 2 as the Danish term to recognize on the "
                    "official page, without treating this as a personal eligibility decision."
                ),
                "citation_ids": [citation_id],
            },
        ]
        if "warning" in question.casefold():
            sections.append(
                {
                    "kind": "source_warning",
                    "text": "Review the cited official source before relying on freshness-sensitive details.",
                    "citation_ids": [citation_id],
                }
            )
        return {
            "summary": "The reviewed source identifies Prøve i Dansk 2 for this supported question.",
            "sections": sections,
        }


CONFIG_PATH = Path(
    os.environ.get("DI_RAG_TEST_CONFIG_PATH", "/tmp/di-rag-browser-provider-config.json")
)
DATA_DIR = Path(os.environ.get("DI_RAG_TEST_DATA_DIR", "/tmp/di-rag-browser-data"))
RELEASE_CATALOG = Path(
    os.environ.get("DI_RAG_TEST_RELEASE_CATALOG", "/tmp/di-rag-browser-release-catalog")
)
if os.environ.get("DI_RAG_TEST_RESET_CONFIG") == "1":
    CONFIG_PATH.unlink(missing_ok=True)
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    shutil.rmtree(RELEASE_CATALOG, ignore_errors=True)

RELEASE_TRUST = create_test_release_trust_fixture(
    DATA_DIR / "test-only-release-trust"
)
EMBEDDING_PROVIDER = DeterministicEmbeddingProviderFixture()
install_minimal_knowledge_release(
    DATA_DIR,
    embedding_provider=EMBEDDING_PROVIDER,
)


def ensure_browser_release_catalog() -> Path:
    release_id = "kr-2026-07-07.1"
    release_dir = RELEASE_CATALOG / release_id
    if not release_dir.exists():
        current_manifest = json.loads(
            (BUNDLED_MINIMAL_RELEASE / "manifest.json").read_text(encoding="utf-8")
        )
        current_documents = json.loads(
            (BUNDLED_MINIMAL_RELEASE / "corpus" / "documents.json").read_text(
                encoding="utf-8"
            )
        )
        sources = []
        for source in current_manifest["sources"]:
            updated = dict(source)
            if updated["source_id"] == "nyidanmark-permanent-residence-language-requirements":
                updated["last_checked_at_utc"] = "2026-07-07T12:00:00Z"
                updated["reviewed_at_utc"] = "2026-07-07T12:30:00Z"
                updated["source_content_sha256"] = (
                    "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
                )
                updated["normalized_document_sha256"] = (
                    "ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100"
                )
            sources.append(updated)

        documents = []
        for document in current_documents:
            updated = dict(document)
            if updated["source_id"] == "nyidanmark-permanent-residence-language-requirements":
                updated["checked_at_utc"] = "2026-07-07T12:00:00Z"
                updated["content"] = updated["content"] + "\nReviewed browser update."
            documents.append(updated)

        build_publishable_knowledge_release(
            release_dir=release_dir,
            release_id=release_id,
            source_registry_version="sr-2026-07-07.1",
            sources=sources,
            documents=documents,
            created_at_utc="2026-07-07T13:00:00Z",
            minimum_application_version="0.1.0",
            signing_private_key_path=RELEASE_TRUST.signing_private_key_path,
            trust_root_path=RELEASE_TRUST.trust_root_path,
        )

    archive_path = RELEASE_CATALOG / f"{release_id}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(release_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(release_dir).as_posix())
    return archive_path


class FixtureGitHubReleaseClient:
    repository = "EricleungDK/Danish-Immigration-Assistant"

    def __init__(self, archive_path: Path) -> None:
        self.archive_path = archive_path
        encoded = archive_path.read_bytes()
        release_id = archive_path.stem
        asset_name = archive_path.name
        self.asset = GitHubReleaseAsset(
            repository=self.repository,
            release_tag=release_id,
            asset_id=81001,
            name=asset_name,
            content_type="application/zip",
            size_bytes=len(encoded),
            browser_download_url=(
                "https://github.com/EricleungDK/Danish-Immigration-Assistant/"
                f"releases/download/{release_id}/{asset_name}"
            ),
            github_sha256=hashlib.sha256(encoded).hexdigest(),
        )
        self.release = GitHubReleaseMetadata(
            repository=self.repository,
            github_release_id=71001,
            tag_name=release_id,
            name="Browser fixture release",
            published_at_utc="2026-07-07T14:00:00Z",
            html_url=(
                "https://github.com/EricleungDK/Danish-Immigration-Assistant/"
                f"releases/tag/{release_id}"
            ),
            assets=(self.asset,),
        )

    def list_published_releases(self) -> tuple[GitHubReleaseMetadata, ...]:
        return (self.release,)

    def download_artifact(
        self,
        asset: GitHubReleaseAsset,
        destination_dir: str | Path,
        *,
        approval: ArtifactDownloadApproval | None,
    ) -> DownloadedReleaseArtifact:
        if approval != ArtifactDownloadApproval(
            approved=True,
            requested_knowledge_release_id=self.release.tag_name,
            artifact_id=self.asset.asset_id,
            artifact_name=self.asset.name,
        ):
            raise ValueError("Browser fixture download lacks exact approval.")
        destination = Path(destination_dir) / asset.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.archive_path, destination)
        encoded = destination.read_bytes()
        return DownloadedReleaseArtifact(
            path=destination,
            bytes_written=len(encoded),
            sha256=hashlib.sha256(encoded).hexdigest(),
            github_digest_verified=True,
        )


GITHUB_RELEASE_CLIENT = FixtureGitHubReleaseClient(ensure_browser_release_catalog())

app = create_app(
    config_path=CONFIG_PATH,
    data_dir=DATA_DIR,
    answer_generator=FixtureAnswerGenerator(),
    capability_tester=fixture_capability_tester,
    embedding_provider=EMBEDDING_PROVIDER,
    trust_root_path=RELEASE_TRUST.trust_root_path,
    github_release_client=GITHUB_RELEASE_CLIENT,
)


@app.post("/__test__/reset-knowledge-release")
async def reset_browser_test_knowledge_release() -> dict[str, str]:
    """Keep stateful update workflows isolated inside the browser test server."""

    dismiss_available_github_knowledge_update(DATA_DIR)
    dismiss_pending_knowledge_update(DATA_DIR)
    installation = install_minimal_knowledge_release(
        DATA_DIR,
        release_dir=BUNDLED_MINIMAL_RELEASE,
        embedding_provider=EMBEDDING_PROVIDER,
    )
    return {
        "knowledge_release_id": str(
            installation["manifest"]["knowledge_release_id"]
        )
    }


def main() -> int:
    port = int(os.environ.get("DI_RAG_BROWSER_PORT", "8917"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
