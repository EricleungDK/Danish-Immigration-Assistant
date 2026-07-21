import asyncio
import hashlib
import json
import shutil
import stat
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

import httpx

from danish_rag.github_release_client import (
    ArtifactDownloadApproval,
    DownloadedReleaseArtifact,
    GitHubReleaseAsset,
    GitHubReleaseMetadata,
)
from danish_rag.knowledge_release import (
    BUNDLED_MINIMAL_RELEASE,
    active_corpus_summary,
    install_minimal_knowledge_release,
    load_pending_knowledge_update,
    prepare_github_knowledge_update,
    save_available_github_knowledge_update,
    select_github_knowledge_update,
)
from danish_rag.local_app import create_app
from danish_rag.source_maintenance import build_publishable_knowledge_release
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture
from tests.release_trust_fixture import create_test_release_trust_fixture


REPOSITORY = "EricleungDK/Danish-Immigration-Assistant"


class FixtureGitHubReleaseClient:
    repository = REPOSITORY

    def __init__(
        self,
        archive_path: Path,
        releases: tuple[GitHubReleaseMetadata, ...] = (),
    ) -> None:
        self.archive_path = archive_path
        self.releases = releases
        self.approvals: list[ArtifactDownloadApproval] = []
        self.list_calls = 0

    def list_published_releases(self) -> tuple[GitHubReleaseMetadata, ...]:
        self.list_calls += 1
        return self.releases

    def download_artifact(
        self,
        asset: GitHubReleaseAsset,
        destination_dir: str | Path,
        *,
        approval: ArtifactDownloadApproval | None,
    ) -> DownloadedReleaseArtifact:
        if approval is None:
            raise AssertionError("approval is required")
        self.approvals.append(approval)
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


class BlockingFixtureGitHubReleaseClient(FixtureGitHubReleaseClient):
    def __init__(self, archive_path, releases=()):
        super().__init__(archive_path, releases)
        self.list_started = threading.Event()
        self.release_list_call = threading.Event()

    def list_published_releases(self):
        self.list_started.set()
        if not self.release_list_call.wait(timeout=2):
            raise TimeoutError("test did not release metadata discovery")
        return super().list_published_releases()


class GitHubKnowledgeUpdateFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.data_dir = self.root / "data"
        self.embedding_provider = DeterministicEmbeddingProviderFixture()
        self.release_trust = create_test_release_trust_fixture(
            self.root / "test-only-release-trust"
        )
        install_minimal_knowledge_release(
            self.data_dir,
            embedding_provider=self.embedding_provider,
        )

    async def wait_for_install_terminal_status(
        self,
        client: httpx.AsyncClient,
    ) -> httpx.Response:
        for _attempt in range(150):
            response = await client.get("/knowledge-updates/install-status")
            if any(
                title in response.text
                for title in (
                    "Knowledge update installed",
                    "Knowledge update rolled back",
                    "Knowledge update needs attention",
                )
            ):
                return response
            await asyncio.sleep(0.02)
        self.fail("knowledge release installation did not reach a terminal status")

    async def wait_for_automatic_check_terminal_status(
        self,
        client: httpx.AsyncClient,
    ) -> httpx.Response:
        for _attempt in range(150):
            response = await client.get(
                "/knowledge-updates/automatic-check-status"
            )
            if any(
                title in response.text
                for title in (
                    "Automatic release metadata check complete",
                    "Automatic release metadata check unavailable",
                )
            ):
                return response
            await asyncio.sleep(0.02)
        self.fail("automatic release metadata check did not reach a terminal status")

    def github_release(
        self,
        release_id: str,
        *,
        archive_path: Path | None = None,
    ) -> GitHubReleaseMetadata:
        asset_name = f"{release_id}.zip"
        if archive_path is None:
            asset_size = 12345
            asset_sha256 = "a" * 64
        else:
            encoded = archive_path.read_bytes()
            asset_size = len(encoded)
            asset_sha256 = hashlib.sha256(encoded).hexdigest()
        return GitHubReleaseMetadata(
            repository=REPOSITORY,
            github_release_id=71001,
            tag_name=release_id,
            name="Untrusted prose deliberately excluded from the update record",
            published_at_utc="2026-07-07T14:00:00Z",
            html_url=(
                "https://github.com/EricleungDK/"
                f"Danish-Immigration-Assistant/releases/tag/{release_id}"
            ),
            assets=(
                GitHubReleaseAsset(
                    repository=REPOSITORY,
                    release_tag=release_id,
                    asset_id=81001,
                    name=asset_name,
                    content_type="application/zip",
                    size_bytes=asset_size,
                    browser_download_url=(
                        "https://github.com/EricleungDK/"
                        "Danish-Immigration-Assistant/releases/download/"
                        f"{release_id}/{asset_name}"
                    ),
                    github_sha256=asset_sha256,
                ),
            ),
        )

    def test_content_free_metadata_discovery_selects_expected_newer_archive_only(self):
        selected = select_github_knowledge_update(
            self.data_dir,
            (self.github_release("kr-2026-07-07.1"),),
        )

        self.assertEqual(selected["release"]["knowledge_release_id"], "kr-2026-07-07.1")
        self.assertEqual(selected["release"]["github_release_id"], 71001)
        self.assertEqual(selected["artifact"]["asset_id"], 81001)
        self.assertEqual(selected["artifact"]["name"], "kr-2026-07-07.1.zip")
        self.assertNotIn("Untrusted prose", str(selected))
        self.assertFalse((self.data_dir / "knowledge-update-staging").exists())
        self.assertFalse((self.data_dir / "corpus" / "kr-2026-07-07.1").exists())

    def make_signed_release_archive(self, release_id: str) -> Path:
        manifest = json.loads(
            (BUNDLED_MINIMAL_RELEASE / "manifest.json").read_text(encoding="utf-8")
        )
        documents = json.loads(
            (BUNDLED_MINIMAL_RELEASE / "corpus" / "documents.json").read_text(
                encoding="utf-8"
            )
        )
        release_dir = self.root / "release" / release_id
        build_publishable_knowledge_release(
            release_dir=release_dir,
            release_id=release_id,
            source_registry_version="sr-2026-07-07.1",
            sources=manifest["sources"],
            documents=documents,
            created_at_utc="2026-07-07T13:00:00Z",
            minimum_application_version="0.1.0",
            signing_private_key_path=self.release_trust.signing_private_key_path,
            trust_root_path=self.release_trust.trust_root_path,
        )
        archive_path = self.root / f"{release_id}.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(release_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(release_dir).as_posix())
        return archive_path

    def test_explicit_approved_download_prepares_signed_summary_without_installing(self):
        release_id = "kr-2026-07-07.1"
        archive_path = self.make_signed_release_archive(release_id)
        release = self.github_release(release_id, archive_path=archive_path)
        available = select_github_knowledge_update(self.data_dir, (release,))
        assert available is not None
        save_available_github_knowledge_update(self.data_dir, available)
        client = FixtureGitHubReleaseClient(archive_path)
        approval = ArtifactDownloadApproval(
            approved=True,
            requested_knowledge_release_id=release_id,
            artifact_id=81001,
            artifact_name=f"{release_id}.zip",
        )

        prepared = prepare_github_knowledge_update(
            self.data_dir,
            client,
            approval=approval,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertEqual(client.approvals, [approval])
        self.assertEqual(prepared["release"]["knowledge_release_id"], release_id)
        self.assertEqual(prepared["distribution"]["channel"], "github-releases")
        self.assertEqual(
            prepared["signed_manifest"]["trust_root_id"],
            self.release_trust.trust_root_id,
        )
        self.assertTrue(prepared["signed_manifest"]["verified"])
        self.assertEqual(load_pending_knowledge_update(self.data_dir), prepared)
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            "kr-2026-07-06.1",
        )
        self.assertFalse((self.data_dir / "corpus" / release_id).exists())
        self.assertTrue(
            (self.data_dir / "knowledge-update-staging" / release_id / "manifest.json").exists()
        )

    def save_github_candidate(
        self,
        release_id: str,
        archive_path: Path,
    ) -> tuple[FixtureGitHubReleaseClient, ArtifactDownloadApproval]:
        release = self.github_release(release_id, archive_path=archive_path)
        available = select_github_knowledge_update(self.data_dir, (release,))
        assert available is not None
        save_available_github_knowledge_update(self.data_dir, available)
        return (
            FixtureGitHubReleaseClient(archive_path),
            ArtifactDownloadApproval(
                approved=True,
                requested_knowledge_release_id=release_id,
                artifact_id=81001,
                artifact_name=f"{release_id}.zip",
            ),
        )

    def test_release_archive_rejects_path_traversal_without_writing_outside_staging(self):
        release_id = "kr-2026-07-07.1"
        archive_path = self.make_signed_release_archive(release_id)
        with zipfile.ZipFile(archive_path, "a") as archive:
            archive.writestr("../escaped.txt", "must not be written")
        client, approval = self.save_github_candidate(release_id, archive_path)

        with self.assertRaisesRegex(Exception, "unsafe path"):
            prepare_github_knowledge_update(
                self.data_dir,
                client,
                approval=approval,
                trust_root_path=self.release_trust.trust_root_path,
            )

        self.assertFalse((self.data_dir / "escaped.txt").exists())
        self.assertFalse((self.root / "escaped.txt").exists())
        self.assertIsNone(load_pending_knowledge_update(self.data_dir))
        self.assertFalse((self.data_dir / "knowledge-update-staging" / release_id).exists())

    def test_release_archive_rejects_symbolic_links(self):
        release_id = "kr-2026-07-07.1"
        archive_path = self.make_signed_release_archive(release_id)
        symlink = zipfile.ZipInfo("corpus/linked-documents.json")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(archive_path, "a") as archive:
            archive.writestr(symlink, "../manifest.json")
        client, approval = self.save_github_candidate(release_id, archive_path)

        with self.assertRaisesRegex(Exception, "symbolic link"):
            prepare_github_knowledge_update(
                self.data_dir,
                client,
                approval=approval,
                trust_root_path=self.release_trust.trust_root_path,
            )

        self.assertIsNone(load_pending_knowledge_update(self.data_dir))
        self.assertFalse((self.data_dir / "knowledge-update-staging" / release_id).exists())

    def test_release_archive_rejects_expanded_size_overflow(self):
        release_id = "kr-2026-07-07.1"
        archive_path = self.make_signed_release_archive(release_id)
        client, approval = self.save_github_candidate(release_id, archive_path)

        with self.assertRaisesRegex(Exception, "expanded-size limit"):
            prepare_github_knowledge_update(
                self.data_dir,
                client,
                approval=approval,
                trust_root_path=self.release_trust.trust_root_path,
                max_expanded_bytes=32,
            )

        self.assertIsNone(load_pending_knowledge_update(self.data_dir))
        self.assertFalse((self.data_dir / "knowledge-update-staging" / release_id).exists())

    def test_download_approval_must_match_exact_tag_asset_id_and_filename(self):
        release_id = "kr-2026-07-07.1"
        archive_path = self.make_signed_release_archive(release_id)
        client, _ = self.save_github_candidate(release_id, archive_path)
        mismatched = ArtifactDownloadApproval(
            approved=True,
            requested_knowledge_release_id=release_id,
            artifact_id=99999,
            artifact_name=f"{release_id}.zip",
        )

        with self.assertRaisesRegex(Exception, "does not match"):
            prepare_github_knowledge_update(
                self.data_dir,
                client,
                approval=mismatched,
                trust_root_path=self.release_trust.trust_root_path,
            )

        self.assertEqual(client.approvals, [])
        self.assertIsNone(load_pending_knowledge_update(self.data_dir))

    def test_signed_manifest_release_id_must_match_approved_github_tag(self):
        approved_release_id = "kr-2026-07-07.1"
        archive_path = self.make_signed_release_archive("kr-2026-07-07.2")
        client, approval = self.save_github_candidate(
            approved_release_id,
            archive_path,
        )

        with self.assertRaisesRegex(Exception, "does not match the approved GitHub tag"):
            prepare_github_knowledge_update(
                self.data_dir,
                client,
                approval=approval,
                trust_root_path=self.release_trust.trust_root_path,
            )

        self.assertIsNone(load_pending_knowledge_update(self.data_dir))
        self.assertFalse(
            (self.data_dir / "knowledge-update-staging" / approved_release_id).exists()
        )

    async def test_app_requires_separate_check_download_and_install_actions(self):
        release_id = "kr-2026-07-07.1"
        archive_path = self.make_signed_release_archive(release_id)
        release = self.github_release(release_id, archive_path=archive_path)
        github_client = FixtureGitHubReleaseClient(archive_path, (release,))
        app = create_app(
            config_path=self.root / "provider-config.json",
            data_dir=self.data_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
            github_release_client=github_client,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)

        checked = await client.post(
            "/knowledge-updates/check",
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

        self.assertEqual(checked.status_code, 303)
        self.assertEqual(github_client.list_calls, 1)
        self.assertEqual(github_client.approvals, [])
        metadata_review = await client.get("/")
        self.assertIn("Knowledge update metadata available", metadata_review.text)
        self.assertIn("No release archive has been downloaded", metadata_review.text)
        self.assertNotIn("Signed manifest verified", metadata_review.text)
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            "kr-2026-07-06.1",
        )

        downloaded = await client.post(
            "/knowledge-updates/download",
            data={
                "release_id": release_id,
                "asset_id": "81001",
                "asset_name": f"{release_id}.zip",
            },
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

        self.assertEqual(downloaded.status_code, 303)
        self.assertEqual(len(github_client.approvals), 1)
        signed_review = await client.get("/")
        self.assertIn("Signed knowledge update ready to review", signed_review.text)
        self.assertIn("Signed manifest verified", signed_review.text)
        self.assertIn(self.release_trust.trust_root_id, signed_review.text)
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            "kr-2026-07-06.1",
        )

        await client.post(
            "/knowledge-updates/check",
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        prepared_only = await client.get("/")
        self.assertIn("Signed knowledge update ready to review", prepared_only.text)
        self.assertNotIn("Knowledge update metadata available", prepared_only.text)

        installed = await client.post(
            "/knowledge-updates/install",
            data={"release_id": release_id},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

        self.assertEqual(installed.status_code, 303)
        install_status = await self.wait_for_install_terminal_status(client)
        self.assertIn("Knowledge update installed", install_status.text)
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            release_id,
        )

    async def test_home_automatically_checks_content_free_metadata_once_per_throttle_window(self):
        release_id = "kr-2026-07-07.1"
        archive_path = self.make_signed_release_archive(release_id)
        release = self.github_release(release_id, archive_path=archive_path)
        github_client = FixtureGitHubReleaseClient(archive_path, (release,))
        app = create_app(
            config_path=self.root / "provider-config.json",
            data_dir=self.data_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
            github_release_client=github_client,
            automatic_update_check_interval_seconds=3600,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)

        home = await client.get("/")
        prefetched = await client.get("/knowledge-updates/automatic-check")
        self.assertEqual(prefetched.status_code, 405)
        self.assertEqual(github_client.list_calls, 0)
        cross_origin = await client.post(
            "/knowledge-updates/automatic-check",
            headers={"Origin": "https://example.invalid"},
        )
        self.assertEqual(cross_origin.status_code, 403)
        self.assertEqual(github_client.list_calls, 0)
        first = await client.post(
            "/knowledge-updates/automatic-check",
            headers={"Origin": "http://testserver"},
        )
        automatic_status = await self.wait_for_automatic_check_terminal_status(client)
        second = await client.post(
            "/knowledge-updates/automatic-check",
            headers={"Origin": "http://testserver"},
        )

        self.assertIn(
            'hx-post="/knowledge-updates/automatic-check"',
            home.text,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(github_client.list_calls, 1)
        self.assertEqual(github_client.approvals, [])
        self.assertIn("Knowledge update metadata available", automatic_status.text)
        self.assertIn("Automatic release metadata check complete", automatic_status.text)
        self.assertIn("Automatic release metadata check recently completed", second.text)
        self.assertNotIn('name="question"', automatic_status.text)
        self.assertNotIn("conversation_id", automatic_status.text)
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            "kr-2026-07-06.1",
        )

    async def test_slow_automatic_check_cannot_overwrite_a_concurrent_dismissal(self):
        release_id = "kr-2026-07-07.1"
        archive_path = self.make_signed_release_archive(release_id)
        release = self.github_release(release_id, archive_path=archive_path)
        github_client = BlockingFixtureGitHubReleaseClient(
            archive_path,
            (release,),
        )
        app = create_app(
            config_path=self.root / "provider-config.json",
            data_dir=self.data_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
            github_release_client=github_client,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)
        origin = {"Origin": "http://testserver"}

        started = await client.post(
            "/knowledge-updates/automatic-check",
            headers=origin,
        )
        self.assertEqual(started.status_code, 200)
        for _attempt in range(100):
            if github_client.list_started.is_set():
                break
            await asyncio.sleep(0.01)
        self.assertTrue(
            github_client.list_started.is_set(),
            "automatic metadata discovery did not start",
        )

        ordinary_home = await asyncio.wait_for(client.get("/"), timeout=0.5)
        conflicting_dismiss = await asyncio.wait_for(
            client.post(
                "/knowledge-updates/dismiss",
                headers=origin,
                follow_redirects=False,
            ),
            timeout=0.5,
        )
        self.assertEqual(ordinary_home.status_code, 200)
        self.assertEqual(conflicting_dismiss.status_code, 409)

        github_client.release_list_call.set()
        automatic_status = await self.wait_for_automatic_check_terminal_status(client)
        self.assertIn("Automatic release metadata check complete", automatic_status.text)
        accepted_dismiss = await client.post(
            "/knowledge-updates/dismiss",
            headers=origin,
            follow_redirects=False,
        )
        self.assertEqual(accepted_dismiss.status_code, 303)
        dismissed_home = await client.get("/")
        self.assertNotIn("Knowledge update metadata available", dismissed_home.text)

    async def test_install_status_exposes_actual_backend_progress_until_completion(self):
        release_id = "kr-2026-07-07.1"
        archive_path = self.make_signed_release_archive(release_id)
        release = self.github_release(release_id, archive_path=archive_path)
        github_client = FixtureGitHubReleaseClient(archive_path, (release,))
        app = create_app(
            config_path=self.root / "provider-config.json",
            data_dir=self.data_dir,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
            github_release_client=github_client,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)
        origin = {"Origin": "http://testserver"}

        await client.post(
            "/knowledge-updates/check",
            headers=origin,
            follow_redirects=False,
        )
        await client.post(
            "/knowledge-updates/download",
            data={
                "release_id": release_id,
                "asset_id": "81001",
                "asset_name": f"{release_id}.zip",
            },
            headers=origin,
            follow_redirects=False,
        )
        started = await client.post(
            "/knowledge-updates/install",
            data={"release_id": release_id},
            headers=origin,
            follow_redirects=False,
        )

        self.assertEqual(started.status_code, 303)
        status = await self.wait_for_install_terminal_status(client)
        self.assertEqual(status.status_code, 200)
        self.assertIn('role="status"', status.text)
        self.assertIn('aria-live="polite"', status.text)
        for phase in (
            "verification",
            "extraction",
            "indexing",
            "embedding",
            "compatibility",
            "activation",
            "complete",
        ):
            self.assertIn(f'data-install-phase="{phase}"', status.text)
        self.assertIn('value="100"', status.text)
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            release_id,
        )

    async def test_automatic_metadata_failure_is_content_free_throttled_and_non_blocking(self):
        class FailingMetadataClient(FixtureGitHubReleaseClient):
            def list_published_releases(self) -> tuple[GitHubReleaseMetadata, ...]:
                self.list_calls += 1
                raise RuntimeError(
                    "PRIVATE-CONTENT-SENTINEL question=do-not-retain"
                )

        github_client = FailingMetadataClient(self.root / "unused.zip")
        app = create_app(
            config_path=self.root / "provider-config.json",
            data_dir=self.data_dir,
            embedding_provider=self.embedding_provider,
            github_release_client=github_client,
            automatic_update_check_interval_seconds=3600,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)
        origin = {"Origin": "http://testserver"}

        started = await client.post(
            "/knowledge-updates/automatic-check",
            headers=origin,
        )
        status = await self.wait_for_automatic_check_terminal_status(client)
        throttled = await client.post(
            "/knowledge-updates/automatic-check",
            headers=origin,
        )
        ordinary_home = await client.get("/")

        self.assertEqual(started.status_code, 200)
        self.assertIn("Automatic release metadata check unavailable", status.text)
        self.assertNotIn("PRIVATE-CONTENT-SENTINEL", status.text)
        self.assertNotIn("do-not-retain", status.text)
        self.assertIn("recently completed", throttled.text)
        self.assertEqual(github_client.list_calls, 1)
        self.assertEqual(github_client.approvals, [])
        self.assertEqual(ordinary_home.status_code, 200)
        self.assertIn("Ask about Danish language requirements", ordinary_home.text)


if __name__ == "__main__":
    unittest.main()
