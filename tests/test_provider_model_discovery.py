import unittest

from danish_rag.local_model_transport import (
    FakeTransport,
    ModelProtocolError,
    ModelTimeoutError,
    ModelUnreachableError,
)
from danish_rag.provider_setup import (
    ProviderConfiguration,
    ProviderModelDiscoverer,
)


def show_payload(capabilities, family, quantization, architecture):
    return {
        "capabilities": capabilities,
        "details": {"family": family, "quantization_level": quantization},
        "model_info": {"general.architecture": architecture},
    }


def discover(transport, provider_id="ollama", endpoint="http://127.0.0.1:11434"):
    built = []

    def factory(factory_provider_id, factory_endpoint, timeout_seconds):
        built.append((factory_provider_id, factory_endpoint))
        return transport

    result = ProviderModelDiscoverer(transport_factory=factory)(
        ProviderConfiguration(provider_id=provider_id, endpoint=endpoint, model="")
    )
    return result, built


class ProviderModelDiscoveryTests(unittest.TestCase):
    def test_ollama_discovery_keeps_only_local_completion_models_matching_contract(self):
        transport = FakeTransport()
        transport.queue_models(
            [
                {"name": "gemma4:26b"},
                {"name": "embeddinggemma"},
                {"name": "other-family:latest"},
                {"name": "gemma4:12b"},
                {"name": "gemma4:cloud"},
            ]
        )
        transport.queue_show(show_payload(["completion"], "gemma4", "Q4_K_M", "gemma4"))
        transport.queue_show(show_payload(["embedding"], "gemma3", "F16", "gemma3"))
        transport.queue_show(show_payload(["completion"], "llama", "Q4_K_M", "llama"))
        transport.queue_show(show_payload(["completion"], "gemma4", "Q4_K_M", "gemma4"))

        result, built = discover(transport)

        self.assertTrue(result.ok)
        self.assertEqual(result.models, ["gemma4:12b", "gemma4:26b"])
        self.assertEqual(built, [("ollama", "http://127.0.0.1:11434")])
        inspected = [r.params["model"] for r in transport.requests if r.operation == "show_model"]
        self.assertEqual(
            inspected, ["gemma4:26b", "embeddinggemma", "other-family:latest", "gemma4:12b"]
        )

    def test_ollama_discovery_skips_models_whose_inspection_fails(self):
        transport = FakeTransport()
        transport.queue_models([{"name": "broken"}, {"name": "gemma4:12b"}])
        transport.queue_error(ModelProtocolError("bad"), operation="show_model")
        transport.queue_show(show_payload(["completion"], "gemma4", "Q4_K_M", "gemma4"))

        result, _built = discover(transport)

        self.assertEqual(result.models, ["gemma4:12b"])

    def test_openai_compatible_discovery_lists_local_provider_model_ids(self):
        transport = FakeTransport()
        transport.queue_models([{"name": "chat-b"}, {"name": "chat-a"}, {"name": "remote:cloud"}])

        result, built = discover(
            transport, provider_id="openai_compatible", endpoint="http://127.0.0.1:1234"
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.models, ["chat-a", "chat-b"])
        self.assertEqual(built, [("openai_compatible", "http://127.0.0.1:1234")])
        self.assertEqual([r.operation for r in transport.requests], ["list_models"])

    def test_transport_errors_map_to_the_existing_unreachable_message(self):
        for error in (ModelUnreachableError("down"), ModelTimeoutError("slow"), ModelProtocolError("junk")):
            with self.subTest(error=type(error).__name__):
                transport = FakeTransport()
                transport.queue_error(error)

                result, _built = discover(transport)

                self.assertFalse(result.ok)
                self.assertEqual(result.reason, "service_unreachable")
                self.assertEqual(
                    result.message,
                    "Start the selected local provider, confirm its loopback endpoint "
                    f"is reachable, and retry model discovery. Detail: {error}",
                )

    def test_no_compatible_models_message_is_unchanged(self):
        transport = FakeTransport()
        transport.queue_models([{"name": "only:cloud"}])

        result, _built = discover(transport)

        self.assertEqual(result.reason, "no_compatible_models")
        self.assertIn("No compatible local generation models were found.", result.message)

    def test_non_loopback_endpoint_is_rejected_before_any_transport_is_built(self):
        result, built = discover(FakeTransport(), endpoint="http://example.com:11434")

        self.assertEqual(result.reason, "invalid_configuration")
        self.assertEqual(built, [])

    def test_default_discovery_refuses_scheme_odd_endpoint_without_network(self):
        result = ProviderModelDiscoverer()(
            ProviderConfiguration(
                provider_id="ollama", endpoint="ftp://127.0.0.1:11434", model=""
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "invalid_configuration")


if __name__ == "__main__":
    unittest.main()
