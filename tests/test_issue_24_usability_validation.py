import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from danish_rag.knowledge_release import (
    BUNDLED_MINIMAL_RELEASE,
    active_corpus_summary,
    install_minimal_knowledge_release,
)
from danish_rag.local_app import create_app
from danish_rag.source_maintenance import build_publishable_knowledge_release
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture
from tests.release_trust_fixture import create_test_release_trust_fixture


class Issue24UsabilityValidationTests(unittest.IsolatedAsyncioTestCase):
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
        install_minimal_knowledge_release(
            self.data_dir,
            embedding_provider=self.embedding_provider,
        )

    def make_newer_release(self, *, release_id: str = "kr-2026-07-07.1") -> None:
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
                updated["content"] = updated["content"] + "\nReviewed issue 24 update."
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

    def make_client(self) -> httpx.AsyncClient:
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
        return client

    async def stage_pending_update(self, client: httpx.AsyncClient) -> None:
        check = await client.post(
            "/knowledge-updates/check",
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        self.assertEqual(check.status_code, 303)

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

    async def test_successful_update_names_the_installed_active_corpus(self):
        self.make_newer_release()
        client = self.make_client()
        await self.stage_pending_update(client)

        response = await client.post(
            "/knowledge-updates/install",
            data={"release_id": "kr-2026-07-07.1"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        status = await self.wait_for_install_terminal_status(client)

        self.assertEqual(response.status_code, 303)
        self.assertIn("Knowledge update installed", status.text)
        self.assertIn("Active corpus: kr-2026-07-07.1", status.text)
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            "kr-2026-07-07.1",
        )

    async def test_failed_update_names_rollback_and_previous_active_corpus(self):
        self.make_newer_release()
        client = self.make_client()
        await self.stage_pending_update(client)

        with patch(
            "danish_rag.local_app.install_knowledge_release",
            side_effect=RuntimeError("simulated indexing failure"),
        ):
            response = await client.post(
                "/knowledge-updates/install",
                data={"release_id": "kr-2026-07-07.1"},
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            )
            status = await self.wait_for_install_terminal_status(client)

        self.assertEqual(response.status_code, 303)
        self.assertIn("Knowledge update rolled back", status.text)
        self.assertIn("previously active corpus/index pair remains active", status.text)
        self.assertIn("Active corpus: kr-2026-07-06.1", status.text)
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            "kr-2026-07-06.1",
        )

    async def test_install_status_does_not_trust_query_release_identity(self):
        client = self.make_client()

        response = await client.get(
            "/?update_status=installed&release_id=kr-not-active"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Knowledge update status expired", response.text)
        self.assertIn("Active corpus: kr-2026-07-06.1", response.text)
        self.assertNotIn("Active corpus: kr-not-active", response.text)


if __name__ == "__main__":
    unittest.main()
