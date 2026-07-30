import unittest

from danish_rag.provider_setup import (
    ProviderConfiguration,
    ProviderModelDiscoverer,
)


class ProviderModelDiscoveryTests(unittest.TestCase):
    def test_ollama_discovery_keeps_only_local_completion_models_matching_contract(self):
        inspected_models = []

        def request_json(endpoint, method, path, payload=None):
            self.assertEqual(endpoint, "http://127.0.0.1:11434")
            if path == "/api/tags":
                return {
                    "models": [
                        {"name": "gemma4:26b"},
                        {"name": "embeddinggemma"},
                        {"name": "other-family:latest"},
                        {"name": "gemma4:12b"},
                        {"name": "gemma4:cloud"},
                    ]
                }
            self.assertEqual(path, "/api/show")
            model = payload["model"]
            inspected_models.append(model)
            if model == "embeddinggemma":
                return {
                    "capabilities": ["embedding"],
                    "details": {
                        "family": "gemma3",
                        "quantization_level": "F16",
                    },
                    "model_info": {"general.architecture": "gemma3"},
                }
            if model == "other-family:latest":
                return {
                    "capabilities": ["completion"],
                    "details": {
                        "family": "llama",
                        "quantization_level": "Q4_K_M",
                    },
                    "model_info": {"general.architecture": "llama"},
                }
            return {
                "capabilities": ["completion"],
                "details": {
                    "family": "gemma4",
                    "quantization_level": "Q4_K_M",
                },
                "model_info": {"general.architecture": "gemma4"},
            }

        result = ProviderModelDiscoverer(request_json=request_json)(
            ProviderConfiguration(
                provider_id="ollama",
                endpoint="http://127.0.0.1:11434",
                model="",
            )
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.models, ["gemma4:12b", "gemma4:26b"])
        self.assertNotIn("gemma4:cloud", inspected_models)

    def test_openai_compatible_discovery_lists_local_provider_model_ids(self):
        def request_json(endpoint, method, path, payload=None):
            self.assertEqual(method, "GET")
            self.assertEqual(path, "/v1/models")
            return {
                "data": [
                    {"id": "chat-b"},
                    {"id": "chat-a"},
                    {"id": "remote:cloud"},
                ]
            }

        result = ProviderModelDiscoverer(request_json=request_json)(
            ProviderConfiguration(
                provider_id="openai_compatible",
                endpoint="http://127.0.0.1:1234",
                model="",
            )
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.models, ["chat-a", "chat-b"])


if __name__ == "__main__":
    unittest.main()
