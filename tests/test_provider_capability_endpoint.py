import unittest

from danish_rag.provider_setup import ProviderCapabilityTester, ProviderConfiguration


class ProviderCapabilityEndpointTests(unittest.TestCase):
    def test_scheme_odd_loopback_endpoint_returns_a_failure_result(self):
        result = ProviderCapabilityTester()._test_ollama(
            ProviderConfiguration(
                provider_id="ollama",
                endpoint="ftp://127.0.0.1:11434",
                model="gemma4:12b",
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "service_unreachable")


if __name__ == "__main__":
    unittest.main()
