import tempfile
import unittest
from pathlib import Path

from danish_rag.knowledge_release import install_minimal_knowledge_release
from danish_rag.retrieval import HybridRetriever
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture


class ProductionEmbeddingContractTests(unittest.TestCase):
    def test_knowledge_release_build_and_retrieval_share_the_embedding_contract(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory) / "data"
            embedding_provider = DeterministicEmbeddingProviderFixture()

            installation = install_minimal_knowledge_release(
                data_dir,
                embedding_provider=embedding_provider,
            )
            index_metadata = installation["index"]

            self.assertEqual(index_metadata["embedding_model"], "embeddinggemma")
            self.assertEqual(index_metadata["embedding_provider"], embedding_provider.provider_id)
            self.assertEqual(index_metadata["vector_dimensions"], 768)
            self.assertEqual(
                index_metadata["embedding_model_identity"]["digest"],
                "sha256:deterministic-embedding-provider-fixture",
            )
            self.assertEqual(index_metadata["rrf_k"], 60)

            results = HybridRetriever.from_data_dir(
                data_dir,
                embedding_provider=embedding_provider,
            ).retrieve("What Danish test do I need for permanent residence?")

            self.assertTrue(results)
            self.assertEqual(
                results[0]["document_id"],
                "di-rag-doc-permanent-residence-language",
            )
            self.assertEqual(
                {call["model"] for call in embedding_provider.embedding_calls},
                {"embeddinggemma"},
            )
            self.assertGreater(len(embedding_provider.embedding_calls), 1)


if __name__ == "__main__":
    unittest.main()
