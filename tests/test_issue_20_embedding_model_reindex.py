import json
import tempfile
import unittest
from pathlib import Path

import httpx

from danish_rag.knowledge_release import (
    active_corpus_summary,
    install_minimal_knowledge_release,
)
from danish_rag.local_app import create_app
from danish_rag.retrieval import (
    HybridRetriever,
    UnsupportedEmbeddingModelError,
)
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture


class Issue20EmbeddingModelReindexTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.data_dir = Path(self.tempdir.name) / "data"
        self.embedding_provider = DeterministicEmbeddingProviderFixture()
        install_minimal_knowledge_release(
            self.data_dir,
            embedding_provider=self.embedding_provider,
        )

    def index_metadata(self) -> dict[str, object]:
        return json.loads(
            (
                self.data_dir
                / "index"
                / "kr-2026-07-06.1"
                / "index-metadata.json"
            ).read_text(encoding="utf-8")
        )

    def assert_active_index_queryable(self, embedding_model: str) -> None:
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            "kr-2026-07-06.1",
        )
        self.assertEqual(self.index_metadata()["embedding_model"], embedding_model)
        results = HybridRetriever.from_data_dir(
            self.data_dir,
            embedding_provider=self.embedding_provider,
        ).retrieve("What Danish test can count for permanent residence?")
        self.assertTrue(results)
        self.assertEqual(results[0]["knowledge_release_id"], "kr-2026-07-06.1")

    def test_unsupported_embedding_model_is_rejected_before_indexing(self):
        with self.assertRaisesRegex(
            UnsupportedEmbeddingModelError,
            "Unsupported embedding model 'unreviewed-local-embedder'",
        ):
            install_minimal_knowledge_release(
                self.data_dir,
                embedding_model="unreviewed-local-embedder",
                embedding_provider=self.embedding_provider,
            )

        self.assert_active_index_queryable("embeddinggemma")
        staged_indexes = list((self.data_dir / ".installing").glob("*/index/*"))
        self.assertEqual(staged_indexes, [])

    def test_same_corpus_can_reindex_to_another_supported_embedding_model(self):
        result = install_minimal_knowledge_release(
            self.data_dir,
            embedding_model="embeddinggemma:latest",
            embedding_provider=self.embedding_provider,
        )

        self.assertEqual(result["index"]["embedding_model"], "embeddinggemma:latest")
        self.assertEqual(result["index"]["vector_dimensions"], 768)
        self.assertEqual(result["index"]["corpus_identity"], "kr-2026-07-06.1")
        self.assertEqual(
            result["index"]["knowledge_release_id"],
            "kr-2026-07-06.1",
        )
        self.assert_active_index_queryable("embeddinggemma:latest")

    def test_failed_reindex_keeps_previous_active_embedding_model_usable(self):
        activation_calls = 0

        def fault_injector(phase: str) -> None:
            nonlocal activation_calls
            if phase != "activation":
                return
            activation_calls += 1
            if activation_calls == 4:
                raise RuntimeError("simulated model activation failure")

        with self.assertRaisesRegex(RuntimeError, "model activation"):
            install_minimal_knowledge_release(
                self.data_dir,
                embedding_model="embeddinggemma:latest",
                embedding_provider=self.embedding_provider,
                fault_injector=fault_injector,
            )

        self.assert_active_index_queryable("embeddinggemma")

    async def test_active_embedding_model_and_corpus_are_visible_without_secrets(self):
        install_minimal_knowledge_release(
            self.data_dir,
            embedding_model="embeddinggemma:latest",
            embedding_provider=self.embedding_provider,
        )
        app = create_app(
            data_dir=self.data_dir,
            config_path=Path(self.tempdir.name) / "provider-config.json",
            embedding_provider=self.embedding_provider,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)

        home = await client.get("/")
        status = await client.get("/status")

        self.assertEqual(home.status_code, 200)
        self.assertIn("kr-2026-07-06.1", home.text)
        self.assertIn("embeddinggemma:latest", home.text)
        self.assertIn("hybrid-index-v1", home.text)
        self.assertNotIn("api_key", home.text)
        self.assertNotIn("secret", home.text.casefold())

        self.assertEqual(status.status_code, 200)
        payload = status.json()
        self.assertEqual(payload["corpus"]["knowledge_release_id"], "kr-2026-07-06.1")
        self.assertEqual(payload["corpus"]["embedding_model"], "embeddinggemma:latest")
        self.assertNotIn("api_key", json.dumps(payload))
        self.assertNotIn("secret", json.dumps(payload).casefold())


if __name__ == "__main__":
    unittest.main()
