import unittest

from danish_rag.embedding_provider import (
    EmbeddingProviderError,
    OllamaEmbeddingProvider,
    canonical_model_identity,
)
from danish_rag.local_model_transport import (
    FakeTransport,
    ModelMissingError,
    ModelProtocolError,
    ModelTimeoutError,
    ModelUnreachableError,
)


def provider_with(fake):
    return OllamaEmbeddingProvider(transport=fake)


class OllamaEmbeddingProviderTests(unittest.TestCase):
    def test_embed_sends_one_text_through_the_transport_and_returns_the_vector(self):
        fake = FakeTransport()
        fake.queue_embed([[1, 2.5, 3]])

        vector = provider_with(fake).embed("embeddinggemma", "hello")

        self.assertEqual(vector, [1.0, 2.5, 3.0])
        self.assertEqual(fake.requests[0].operation, "embed")
        self.assertEqual(
            fake.requests[0].params, {"model": "embeddinggemma", "texts": ["hello"]}
        )

    def test_embed_rejects_invalid_vectors(self):
        for vector in ([], [float("nan")], ["x"], [True]):
            with self.subTest(vector=vector):
                fake = FakeTransport()
                fake.queue_embed([vector])
                with self.assertRaisesRegex(EmbeddingProviderError, "invalid embedding"):
                    provider_with(fake).embed("embeddinggemma", "hello")

    def test_embed_wraps_every_transport_failure_with_recovery_guidance(self):
        for error in (
            ModelUnreachableError("down"),
            ModelTimeoutError("slow"),
            ModelMissingError("gone"),
            ModelProtocolError("bad"),
        ):
            with self.subTest(error=type(error).__name__):
                fake = FakeTransport()
                fake.queue_error(error)
                with self.assertRaisesRegex(
                    EmbeddingProviderError, "ollama pull embeddinggemma"
                ):
                    provider_with(fake).embed("embeddinggemma", "hello")

    def test_inspect_model_returns_canonical_identity_with_fingerprint(self):
        fake = FakeTransport()
        fake.queue_show(
            {
                "model": "embeddinggemma",
                "digest": "sha256:abc",
                "details": {"family": "gemma3"},
                "unrelated": "dropped",
            }
        )

        identity = provider_with(fake).inspect_model("embeddinggemma")

        self.assertEqual(identity["digest"], "sha256:abc")
        self.assertNotIn("unrelated", identity)
        self.assertEqual(
            identity,
            canonical_model_identity(
                {
                    "model": "embeddinggemma",
                    "digest": "sha256:abc",
                    "details": {"family": "gemma3"},
                }
            ),
        )
        self.assertEqual([r.operation for r in fake.requests], ["show_model"])

    def test_inspect_model_fills_digest_from_installed_model_list(self):
        fake = FakeTransport()
        fake.queue_show({"model": "embeddinggemma", "details": {"family": "gemma3"}})
        fake.queue_models(
            [
                {"name": "embeddinggemma:latest", "digest": "sha256:tag", "modified_at": "t"},
                {"name": "other:1", "digest": "sha256:other"},
            ]
        )

        identity = provider_with(fake).inspect_model("embeddinggemma")

        self.assertEqual(identity["digest"], "sha256:tag")
        self.assertEqual(identity["modified_at"], "t")

    def test_inspect_model_survives_a_failing_model_list(self):
        fake = FakeTransport()
        fake.queue_show({"model": "embeddinggemma"})
        fake.queue_error(ModelUnreachableError("down"), operation="list_models")

        identity = provider_with(fake).inspect_model("embeddinggemma")

        self.assertNotIn("digest", identity)

    def test_inspect_missing_model_tells_the_user_to_pull_it(self):
        fake = FakeTransport()
        fake.queue_error(ModelMissingError("gone"), operation="show_model")
        with self.assertRaisesRegex(
            EmbeddingProviderError, "not installed in Ollama.*ollama pull embeddinggemma"
        ):
            provider_with(fake).inspect_model("embeddinggemma")

    def test_inspect_other_failures_mention_the_endpoint_and_detail(self):
        fake = FakeTransport()
        fake.queue_error(ModelUnreachableError("Ollama is unreachable"), operation="show_model")
        with self.assertRaisesRegex(
            EmbeddingProviderError, "Could not inspect.*127.0.0.1:11434.*unreachable"
        ):
            provider_with(fake).inspect_model("embeddinggemma")

    def test_non_loopback_endpoint_is_rejected_as_an_embedding_provider_error(self):
        with self.assertRaisesRegex(EmbeddingProviderError, "loopback"):
            OllamaEmbeddingProvider("http://example.com:11434")


if __name__ == "__main__":
    unittest.main()
