import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import httpx

from danish_rag.knowledge_release import (
    BUNDLED_MINIMAL_RELEASE,
    active_corpus_summary,
    discover_knowledge_update,
    install_minimal_knowledge_release,
)
from danish_rag.local_app import create_app
from danish_rag.source_maintenance import build_publishable_knowledge_release
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture
from tests.release_trust_fixture import create_test_release_trust_fixture


class Issue18KnowledgeUpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.data_dir = self.root / "data"
        self.release_catalog = self.root / "release-catalog"
        self.embedding_provider = DeterministicEmbeddingProviderFixture()
        self.release_trust = create_test_release_trust_fixture(
            self.root / "test-only-release-trust"
        )
        self.current = install_minimal_knowledge_release(
            self.data_dir,
            embedding_provider=self.embedding_provider,
        )

    async def wait_for_install_terminal_status(
        self, client: httpx.AsyncClient
    ) -> httpx.Response:
        for _ in range(150):
            status = await client.get("/knowledge-updates/install-status")
            if any(
                message in status.text
                for message in {
                    "Knowledge update installed",
                    "Knowledge update rolled back",
                    "Knowledge update needs attention",
                }
            ):
                return status
            await asyncio.sleep(0.02)
        self.fail("Knowledge installation did not reach a terminal state")

    def make_newer_release(
        self,
        *,
        release_id: str = "kr-2026-07-07.1",
        extraction_identity_only: bool = False,
    ) -> Path:
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
                if extraction_identity_only:
                    updated["normalized_extraction_sha256"] = "a" * 64
                else:
                    updated["last_checked_at_utc"] = "2026-07-07T12:00:00Z"
                    updated["reviewed_at_utc"] = "2026-07-07T12:30:00Z"
                    updated["source_content_sha256"] = (
                        "00112233445566778899aabbccddeeff"
                        "00112233445566778899aabbccddeeff"
                    )
                    updated["normalized_document_sha256"] = (
                        "ffeeddccbbaa99887766554433221100"
                        "ffeeddccbbaa99887766554433221100"
                    )
            sources.append(updated)

        documents = []
        for document in current_documents:
            updated = dict(document)
            if (
                not extraction_identity_only
                and updated["source_id"]
                == "nyidanmark-permanent-residence-language-requirements"
            ):
                updated["checked_at_utc"] = "2026-07-07T12:00:00Z"
                updated["content"] = updated["content"] + "\nReviewed July update."
            documents.append(updated)

        build_publishable_knowledge_release(
            release_dir=self.release_catalog / release_id,
            release_id=release_id,
            source_registry_version="sr-2026-07-07.1",
            sources=sources,
            documents=documents,
            created_at_utc="2026-07-07T13:00:00Z",
            minimum_application_version="0.1.0",
            signing_private_key_path=self.release_trust.signing_private_key_path,
            trust_root_path=self.release_trust.trust_root_path,
        )
        return self.release_catalog / release_id

    def test_discovery_summarizes_newer_compatible_release_without_installing_artifacts(self):
        self.make_newer_release()

        update = discover_knowledge_update(
            self.data_dir,
            self.release_catalog,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertIsNotNone(update)
        assert update is not None
        self.assertEqual(update["release"]["knowledge_release_id"], "kr-2026-07-07.1")
        self.assertEqual(update["compatibility"]["status"], "compatible")
        self.assertEqual(update["reviewed_source_changes"]["updated"], 1)
        self.assertEqual(update["reviewed_source_changes"]["added"], 0)
        self.assertEqual(update["reviewed_source_changes"]["removed"], 0)
        self.assertEqual(
            update["reviewed_source_changes"]["updated_sources"][0]["title"],
            "Permanent residence language requirements",
        )
        self.assertEqual(update["expected_local_indexing_work"]["document_count"], 5)
        self.assertGreater(update["expected_local_indexing_work"]["artifact_bytes"], 0)
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            "kr-2026-07-06.1",
        )
        self.assertFalse((self.data_dir / "corpus" / "kr-2026-07-07.1").exists())
        self.assertFalse((self.data_dir / "index" / "kr-2026-07-07.1").exists())
        update_json = json.dumps(update)
        for private_marker in {
            "What Danish test do I need?",
            "The reviewed source identifies",
            "conversation_id",
            "turn_index",
            "citation_id",
        }:
            self.assertNotIn(private_marker, update_json)

    def test_discovery_reports_reviewed_extraction_identity_changes(self):
        self.make_newer_release(extraction_identity_only=True)

        update = discover_knowledge_update(
            self.data_dir,
            self.release_catalog,
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertIsNotNone(update)
        assert update is not None
        self.assertEqual(update["reviewed_source_changes"]["updated"], 1)
        self.assertEqual(
            update["reviewed_source_changes"]["updated_sources"][0]["source_id"],
            "nyidanmark-permanent-residence-language-requirements",
        )

    async def test_manual_check_returns_announced_fragment_for_empty_and_available_updates(self):
        self.release_catalog.mkdir(parents=True, exist_ok=True)
        app = create_app(
            config_path=self.root / "provider-config.json", data_dir=self.data_dir,
            release_catalog_dir=self.release_catalog, embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            for available in (False, True):
                if available:
                    self.make_newer_release()
                response = await client.post("/knowledge-updates/check", headers={"Origin": "http://testserver", "HX-Request": "true"})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("<html", response.text)
                self.assertIn('id="manual-update-result"', response.text)
                self.assertIn("Knowledge update available" if available else "No newer knowledge update is available", response.text)

    async def test_manual_check_errors_are_visible_and_origin_guard_is_preserved(self):
        app = create_app(
            config_path=self.root / "provider-config.json", data_dir=self.data_dir,
            release_catalog_dir=self.release_catalog, embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            for error in (HTTPException(status_code=409, detail="Installation is in progress."), RuntimeError("unavailable")):
                with patch("danish_rag.local_app.discover_knowledge_update", side_effect=error):
                    response = await client.post("/knowledge-updates/check", headers={"Origin": "http://testserver", "HX-Request": "true"})
                self.assertEqual(response.status_code, 200)
                self.assertIn('id="manual-update-result"', response.text)
                self.assertNotIn("No newer knowledge update is available", response.text)
                self.assertIn("Installation is in progress." if isinstance(error, HTTPException) else "Knowledge update failed while checking", response.text)
            rejected = await client.post("/knowledge-updates/check", headers={"HX-Request": "true"})
            self.assertEqual(rejected.status_code, 403)

    async def test_app_review_dismiss_and_install_controls_preserve_explicit_user_approval(self):
        self.make_newer_release()
        app = create_app(
            config_path=self.root / "provider-config.json",
            data_dir=self.data_dir,
            release_catalog_dir=self.release_catalog,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)

        check = await client.post(
            "/knowledge-updates/check",
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

        self.assertEqual(check.status_code, 303)
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            "kr-2026-07-06.1",
        )
        review = await client.get("/")
        self.assertIn("Knowledge update available", review.text)
        self.assertIn("kr-2026-07-07.1", review.text)
        self.assertIn("Compatible with this application", review.text)
        self.assertIn("Reviewed source changes", review.text)
        self.assertIn("Permanent residence language requirements", review.text)
        self.assertIn("Expected local indexing work", review.text)

        dismiss = await client.post(
            "/knowledge-updates/dismiss",
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        self.assertEqual(dismiss.status_code, 303)
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            "kr-2026-07-06.1",
        )
        dismissed_home = await client.get("/")
        self.assertNotIn("Knowledge update available", dismissed_home.text)

        await client.post(
            "/knowledge-updates/check",
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        install = await client.post(
            "/knowledge-updates/install",
            data={"release_id": "kr-2026-07-07.1"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        self.assertEqual(install.status_code, 303)
        installation_status = await self.wait_for_install_terminal_status(client)
        self.assertIn("Knowledge update installed", installation_status.text)
        self.assertEqual(
            active_corpus_summary(
                self.data_dir,
                trust_root_path=self.release_trust.trust_root_path,
            )["knowledge_release_id"],
            "kr-2026-07-07.1",
        )
        self.assertTrue((self.data_dir / "index" / "kr-2026-07-07.1").exists())


if __name__ == "__main__":
    unittest.main()
