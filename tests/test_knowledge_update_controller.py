"""Direct tests of the Knowledge update controller (#78), without HTTP."""

import hashlib
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from danish_rag.github_release_client import (
    ArtifactDownloadApproval,
    GitHubReleaseAsset,
    GitHubReleaseMetadata,
)
from danish_rag.knowledge_release import (
    BUNDLED_MINIMAL_RELEASE,
    active_corpus_summary,
    discover_knowledge_update,
    ensure_minimal_knowledge_release,
    install_minimal_knowledge_release,
    load_available_github_knowledge_update,
    load_pending_knowledge_update,
    save_pending_knowledge_update,
)
from danish_rag.knowledge_update_controller import (
    KnowledgeUpdateConflict,
    KnowledgeUpdateController,
    KnowledgeUpdateUnavailable,
)
from danish_rag.source_maintenance import build_publishable_knowledge_release
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture
from tests.release_trust_fixture import create_test_release_trust_fixture
from tests.test_github_knowledge_update_flow import (
    BlockingFixtureGitHubReleaseClient,
    FixtureGitHubReleaseClient,
    REPOSITORY,
)

PRIOR = "kr-2026-07-06.1"
NEWER = "kr-2026-07-07.1"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ControllerTestBase(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.root = Path(tempdir.name)
        self.data_dir = self.root / "data"
        self.catalog = self.root / "catalog"
        self.provider = DeterministicEmbeddingProviderFixture()
        self.trust = create_test_release_trust_fixture(self.root / "trust")
        self.clock = FakeClock()
        install_minimal_knowledge_release(self.data_dir, embedding_provider=self.provider)

    def build_release(self, release_dir: Path, release_id: str) -> Path:
        manifest = json.loads((BUNDLED_MINIMAL_RELEASE / "manifest.json").read_text(encoding="utf-8"))
        documents = json.loads(
            (BUNDLED_MINIMAL_RELEASE / "corpus" / "documents.json").read_text(encoding="utf-8")
        )
        documents = [dict(d) for d in documents]
        documents[0]["content"] += "\nReviewed July update."
        build_publishable_knowledge_release(
            release_dir=release_dir,
            release_id=release_id,
            source_registry_version="sr-2026-07-07.1",
            sources=manifest["sources"],
            documents=documents,
            created_at_utc="2026-07-07T13:00:00Z",
            minimum_application_version="0.1.0",
            signing_private_key_path=self.trust.signing_private_key_path,
            trust_root_path=self.trust.trust_root_path,
        )
        return release_dir

    def github_release(self, release_id: str, archive: Path | None = None) -> GitHubReleaseMetadata:
        name = f"{release_id}.zip"
        data = archive.read_bytes() if archive else b""
        return GitHubReleaseMetadata(
            repository=REPOSITORY,
            github_release_id=71001,
            tag_name=release_id,
            name="ignored",
            published_at_utc="2026-07-07T14:00:00Z",
            html_url=f"https://github.com/{REPOSITORY}/releases/tag/{release_id}",
            assets=(
                GitHubReleaseAsset(
                    repository=REPOSITORY,
                    release_tag=release_id,
                    asset_id=81001,
                    name=name,
                    content_type="application/zip",
                    size_bytes=len(data) if archive else 12345,
                    browser_download_url=(
                        f"https://github.com/{REPOSITORY}/releases/download/{release_id}/{name}"
                    ),
                    github_sha256=hashlib.sha256(data).hexdigest() if archive else "a" * 64,
                ),
            ),
        )

    def make_controller(self, *, client=None, fault_injector=None, interval=60.0):
        return KnowledgeUpdateController(
            data_dir=self.data_dir,
            release_catalog_dir=self.catalog,
            github_release_client=client,
            embedding_provider=self.provider,
            trust_root_path=self.trust.trust_root_path,
            ensure_release=lambda: ensure_minimal_knowledge_release(
                self.data_dir,
                embedding_provider=self.provider,
                trust_root_path=self.trust.trust_root_path,
            ),
            automatic_check_interval_seconds=interval,
            clock=self.clock,
            install_fault_injector=fault_injector,
        )

    def stage_local_pending(self, release_id: str = NEWER) -> None:
        self.build_release(self.catalog / release_id, release_id)
        update = discover_knowledge_update(
            self.data_dir, self.catalog, trust_root_path=self.trust.trust_root_path
        )
        save_pending_knowledge_update(self.data_dir, update)

    def active_release_id(self) -> str:
        return active_corpus_summary(
            self.data_dir, trust_root_path=self.trust.trust_root_path
        )["knowledge_release_id"]


class ManualCheckTests(ControllerTestBase):
    def test_local_catalog_check_saves_pending_update(self):
        self.build_release(self.catalog / NEWER, NEWER)
        controller = self.make_controller()
        controller.manual_check()
        pending = load_pending_knowledge_update(self.data_dir)
        self.assertEqual(pending["release"]["knowledge_release_id"], NEWER)

    def test_github_check_saves_available_update_from_fake_client(self):
        client = FixtureGitHubReleaseClient(self.root / "unused.zip", (self.github_release(NEWER),))
        self.make_controller(client=client).manual_check()
        self.assertEqual(client.list_calls, 1)
        available = load_available_github_knowledge_update(self.data_dir)
        self.assertEqual(available["release"]["knowledge_release_id"], NEWER)

    def test_check_failure_is_unavailable_and_names_unchanged_corpus(self):
        class Failing(FixtureGitHubReleaseClient):
            def list_published_releases(self):
                raise RuntimeError("offline")

        controller = self.make_controller(client=Failing(self.root / "x.zip"))
        with self.assertRaises(KnowledgeUpdateUnavailable) as caught:
            controller.manual_check()
        self.assertIn("Knowledge update failed while checking", str(caught.exception))
        self.assertIn("previously active corpus/index pair remains active", str(caught.exception))
        self.assertEqual(self.active_release_id(), PRIOR)

    def test_check_rejected_while_another_update_action_holds_the_record_lock(self):
        client = BlockingFixtureGitHubReleaseClient(self.root / "x.zip", (self.github_release(NEWER),))
        controller = self.make_controller(client=client)
        first = threading.Thread(target=controller.manual_check)
        first.start()
        self.assertTrue(client.list_started.wait(timeout=2))
        try:
            with self.assertRaisesRegex(KnowledgeUpdateConflict, "Another knowledge update action"):
                controller.manual_check()
            with self.assertRaisesRegex(KnowledgeUpdateConflict, "Another knowledge update action"):
                controller.dismiss()
        finally:
            client.release_list_call.set()
            first.join(timeout=2)
        controller.dismiss()

    def test_dismiss_clears_pending_and_available_records(self):
        self.stage_local_pending()
        controller = self.make_controller()
        controller.dismiss()
        self.assertIsNone(load_pending_knowledge_update(self.data_dir))


class AutomaticCheckTests(ControllerTestBase):
    def client(self):
        return FixtureGitHubReleaseClient(self.root / "x.zip", (self.github_release(NEWER),))

    def test_disabled_for_local_catalog(self):
        controller = self.make_controller()
        self.assertFalse(controller.automatic_check_enabled)
        status = controller.request_automatic_check()
        self.assertEqual(status["state"], "unavailable")
        self.assertIsNone(controller.automatic_check_status())

    def test_runs_once_then_throttles_until_interval_elapses(self):
        client = self.client()
        controller = self.make_controller(client=client, interval=60)
        self.assertEqual(controller.request_automatic_check()["state"], "running")
        self.assertTrue(controller.wait_idle(timeout=5))
        self.assertEqual(controller.automatic_check_status()["state"], "completed")
        self.assertEqual(client.list_calls, 1)

        self.clock.now += 59
        throttled = controller.request_automatic_check()
        self.assertEqual(throttled["title"], "Automatic release metadata check recently completed")
        self.assertEqual(client.list_calls, 1)

        self.clock.now += 1
        self.assertEqual(controller.request_automatic_check()["state"], "running")
        self.assertTrue(controller.wait_idle(timeout=5))
        self.assertEqual(client.list_calls, 2)

    def test_second_request_while_running_does_not_start_another_worker(self):
        client = BlockingFixtureGitHubReleaseClient(self.root / "x.zip", (self.github_release(NEWER),))
        controller = self.make_controller(client=client)
        controller.request_automatic_check()
        self.assertTrue(client.list_started.wait(timeout=2))
        self.assertTrue(controller.automatic_check_running)
        again = controller.request_automatic_check()
        self.assertEqual(again["title"], "Automatic release metadata check already running")
        client.release_list_call.set()
        self.assertTrue(controller.wait_idle(timeout=5))
        self.assertEqual(client.list_calls, 1)

    def test_failure_status_is_content_free_and_throttled(self):
        class Failing(FixtureGitHubReleaseClient):
            def list_published_releases(self):
                self.list_calls += 1
                raise RuntimeError("secret-detail")

        client = Failing(self.root / "x.zip")
        controller = self.make_controller(client=client)
        controller.request_automatic_check()
        self.assertTrue(controller.wait_idle(timeout=5))
        status = controller.automatic_check_status()
        self.assertEqual(status["state"], "failed")
        self.assertNotIn("secret-detail", json.dumps(status))
        controller.request_automatic_check()
        self.assertEqual(client.list_calls, 1)

    def test_reset_clears_throttle_and_status(self):
        client = self.client()
        controller = self.make_controller(client=client)
        controller.request_automatic_check()
        controller.wait_idle(timeout=5)
        controller.reset_automatic_check()
        self.assertIsNone(controller.automatic_check_status())
        self.assertEqual(controller.request_automatic_check()["state"], "running")
        controller.wait_idle(timeout=5)

    def test_invalid_interval_rejected(self):
        for bad in (0, -1, True, "5"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.make_controller(interval=bad)


class InstallTests(ControllerTestBase):
    def blocking_injector(self):
        started, release = threading.Event(), threading.Event()

        def inject(phase: str) -> None:
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release installer")

        return started, release, inject

    def test_install_reports_progress_and_activates_release(self):
        self.stage_local_pending()
        controller = self.make_controller()
        self.assertIsNone(controller.installation_status())
        controller.start_install(NEWER)
        self.assertTrue(controller.wait_idle(timeout=30))
        status = controller.installation_status()
        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["active_release_id"], NEWER)
        self.assertEqual(status["previous_release_id"], PRIOR)
        self.assertFalse(status["rolled_back"])
        phases = [event["phase"] for event in status["events"]]
        self.assertEqual(phases[-1], "complete")
        self.assertEqual(status["events"][-1]["percent"], 100)
        self.assertIsNone(load_pending_knowledge_update(self.data_dir))
        self.assertEqual(self.active_release_id(), NEWER)

    def test_status_snapshot_is_a_copy(self):
        self.stage_local_pending()
        controller = self.make_controller()
        controller.start_install(NEWER)
        controller.wait_idle(timeout=30)
        snapshot = controller.installation_status()
        snapshot["events"].clear()
        self.assertTrue(controller.installation_status()["events"])

    def test_double_install_rejected_while_running(self):
        self.stage_local_pending()
        started, release, inject = self.blocking_injector()
        controller = self.make_controller(fault_injector=inject)
        controller.start_install(NEWER)
        self.assertTrue(started.wait(timeout=5))
        self.assertTrue(controller.installation_is_running())
        try:
            with self.assertRaisesRegex(KnowledgeUpdateConflict, "already running"):
                controller.start_install(NEWER)
        finally:
            release.set()
        self.assertTrue(controller.wait_idle(timeout=30))
        self.assertEqual(controller.installation_status()["state"], "completed")

    def test_check_and_dismiss_rejected_while_installing(self):
        self.stage_local_pending()
        started, release, inject = self.blocking_injector()
        controller = self.make_controller(fault_injector=inject)
        controller.start_install(NEWER)
        self.assertTrue(started.wait(timeout=5))
        try:
            with self.assertRaisesRegex(
                KnowledgeUpdateConflict, "Wait for the current knowledge release installation"
            ):
                controller.manual_check()
            with self.assertRaisesRegex(
                KnowledgeUpdateConflict, "Wait for the current knowledge release installation"
            ):
                controller.dismiss()
        finally:
            release.set()
            controller.wait_idle(timeout=30)

    def test_automatic_check_deferred_while_installing(self):
        self.stage_local_pending()
        started, release, inject = self.blocking_injector()
        client = FixtureGitHubReleaseClient(
            self.root / "x.zip", (self.github_release("kr-2026-07-08.1"),)
        )
        controller = self.make_controller(client=client, fault_injector=inject)
        controller.start_install(NEWER)
        self.assertTrue(started.wait(timeout=5))
        try:
            controller.request_automatic_check()
            waiter = threading.Event()
            for _ in range(200):
                if not controller.automatic_check_running:
                    break
                waiter.wait(0.01)
            self.assertEqual(controller.automatic_check_status()["state"], "deferred")
            self.assertEqual(client.list_calls, 0)
        finally:
            release.set()
            controller.wait_idle(timeout=30)

    def test_install_rejects_missing_or_mismatched_pending_release(self):
        controller = self.make_controller()
        with self.assertRaisesRegex(KnowledgeUpdateConflict, "No reviewed knowledge update is pending"):
            controller.start_install(NEWER)
        self.stage_local_pending()
        with self.assertRaisesRegex(KnowledgeUpdateConflict, "not pending review"):
            controller.start_install("kr-2099-01-01.1")
        self.assertIsNone(controller.installation_status())

    def test_fault_at_every_phase_keeps_prior_release_active(self):
        for phase in ("verification", "extraction", "indexing", "embedding", "compatibility", "activation"):
            with self.subTest(phase=phase):
                self.setUp()
                self.stage_local_pending()

                def inject(current: str, phase=phase) -> None:
                    if current == phase:
                        raise RuntimeError(f"simulated {phase} failure")

                controller = self.make_controller(fault_injector=inject)
                controller.start_install(NEWER)
                self.assertTrue(controller.wait_idle(timeout=30))
                status = controller.installation_status()
                self.assertEqual(status["state"], "failed")
                self.assertEqual(status["error_type"], "RuntimeError")
                self.assertTrue(status["rolled_back"])
                self.assertEqual(status["active_release_id"], PRIOR)
                self.assertEqual(self.active_release_id(), PRIOR)
                self.assertIsNotNone(load_pending_knowledge_update(self.data_dir))

    def test_new_install_allowed_after_failure(self):
        self.stage_local_pending()
        calls = []

        def inject(phase: str) -> None:
            calls.append(phase)
            if len(calls) == 1:
                raise RuntimeError("first attempt fails")

        controller = self.make_controller(fault_injector=inject)
        controller.start_install(NEWER)
        controller.wait_idle(timeout=30)
        self.assertEqual(controller.installation_status()["state"], "failed")
        controller.start_install(NEWER)
        controller.wait_idle(timeout=30)
        self.assertEqual(controller.installation_status()["state"], "completed")


class DownloadTests(ControllerTestBase):
    def make_archive(self, release_id: str) -> Path:
        release_dir = self.build_release(self.root / "built" / release_id, release_id)
        archive = self.root / f"{release_id}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(release_dir.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(release_dir).as_posix())
        return archive

    def test_download_disabled_for_local_catalog(self):
        controller = self.make_controller()
        self.assertFalse(controller.github_downloads_enabled)
        with self.assertRaisesRegex(KnowledgeUpdateConflict, "not enabled"):
            controller.approve_download(release_id=NEWER, asset_id=81001, asset_name=f"{NEWER}.zip")

    def test_approved_download_prepares_pending_update_then_installs(self):
        archive = self.make_archive(NEWER)
        client = FixtureGitHubReleaseClient(archive, (self.github_release(NEWER, archive),))
        controller = self.make_controller(client=client)
        controller.manual_check()
        controller.approve_download(release_id=NEWER, asset_id=81001, asset_name=f"{NEWER}.zip")
        self.assertEqual(client.approvals[0].requested_knowledge_release_id, NEWER)
        self.assertIsInstance(client.approvals[0], ArtifactDownloadApproval)
        pending = load_pending_knowledge_update(self.data_dir)
        self.assertEqual(pending["distribution"]["channel"], "github-releases")
        self.assertEqual(self.active_release_id(), PRIOR)
        controller.start_install(NEWER)
        self.assertTrue(controller.wait_idle(timeout=30))
        self.assertEqual(controller.installation_status()["state"], "completed")
        self.assertEqual(self.active_release_id(), NEWER)

    def test_mismatched_approval_is_unavailable_and_changes_nothing(self):
        archive = self.make_archive(NEWER)
        client = FixtureGitHubReleaseClient(archive, (self.github_release(NEWER, archive),))
        controller = self.make_controller(client=client)
        controller.manual_check()
        with self.assertRaises(KnowledgeUpdateUnavailable) as caught:
            controller.approve_download(release_id=NEWER, asset_id=999, asset_name=f"{NEWER}.zip")
        self.assertIn("downloading and verifying", str(caught.exception))
        self.assertIsNone(load_pending_knowledge_update(self.data_dir))
        self.assertEqual(self.active_release_id(), PRIOR)


if __name__ == "__main__":
    unittest.main()
