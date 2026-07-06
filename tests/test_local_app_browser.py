import tempfile
import unittest
from pathlib import Path

import httpx

from danish_rag.local_app import create_app


class BrowserLevelApplicationTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, capability_tester=None):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        config_path = Path(self.tempdir.name) / "provider-config.json"
        data_dir = Path(self.tempdir.name) / "data"
        app = create_app(
            config_path=config_path,
            data_dir=data_dir,
            capability_tester=capability_tester,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)
        return client, config_path

    def openai_setup_payload(self, model="local-model"):
        return {
            "provider_id": "openai_compatible",
            "endpoint": "http://127.0.0.1:1234",
            "model": model,
        }

    def ollama_setup_payload(self):
        return {
            "provider_id": "ollama",
            "endpoint": "http://127.0.0.1:11434",
            "model": "gemma4:12b",
        }

    async def test_first_launch_shows_product_boundaries_setup_and_multiline_composer(self):
        client, _ = self.make_client()

        response = await client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Danish Immigration RAG", html)
        self.assertIn("information assistant, not an authority or lawyer", html)
        self.assertIn("local-only answer path", html)
        self.assertIn("name=\"question\"", html)
        self.assertIn("<textarea", html)
        self.assertIn("rows=\"4\"", html)
        self.assertIn("Ollama", html)
        self.assertIn("OpenAI-compatible local server", html)

    async def test_failed_setup_preserves_non_secret_fields_and_actionable_error(self):
        def failing_tester(configuration):
            return {
                "ok": False,
                "reason": "service_unreachable",
                "message": "Provider service is unreachable. Start the local server and retry.",
            }

        client, config_path = self.make_client(capability_tester=failing_tester)

        response = await client.post(
            "/setup",
            data=self.openai_setup_payload(),
            headers={"Origin": "http://testserver"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(config_path.exists())
        html = response.text
        self.assertIn("Provider service is unreachable", html)
        self.assertIn("Start the local server and retry", html)
        self.assertIn("value=\"http://127.0.0.1:1234\"", html)
        self.assertIn("value=\"local-model\"", html)
        self.assertNotIn("secret", html.casefold())

    async def test_successful_setup_survives_restart_and_shows_runtime_without_secrets(self):
        def passing_tester(configuration):
            return {
                "ok": True,
                "provider_version": "0.9.9-fixture",
                "model_identity": {"family": "fixture"},
                "capabilities": ["generation"],
            }

        client, config_path = self.make_client(capability_tester=passing_tester)

        response = await client.post(
            "/setup",
            data=self.openai_setup_payload(),
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertTrue(config_path.exists())

        restarted = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(config_path=config_path)),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(restarted.aclose)
        home = await restarted.get("/")

        self.assertEqual(home.status_code, 200)
        self.assertIn("OpenAI-compatible local server", home.text)
        self.assertIn("local-model", home.text)
        self.assertIn("0.9.9-fixture", home.text)
        self.assertNotIn("api_key", home.text)
        self.assertNotIn("secret", home.text.casefold())

    async def test_state_changing_requests_reject_non_loopback_host_and_mismatched_origin(self):
        client, _ = self.make_client()

        bad_host = await client.post(
            "/setup",
            data=self.ollama_setup_payload(),
            headers={"Host": "example.com", "Origin": "http://example.com"},
        )
        self.assertEqual(bad_host.status_code, 403)

        bad_origin = await client.post(
            "/setup",
            data=self.ollama_setup_payload(),
            headers={"Host": "testserver", "Origin": "http://evil.example"},
        )
        self.assertEqual(bad_origin.status_code, 403)

        missing_origin = await client.post(
            "/setup",
            data=self.ollama_setup_payload(),
        )
        self.assertEqual(missing_origin.status_code, 403)

        mismatched_port = await client.post(
            "/setup",
            data=self.ollama_setup_payload(),
            headers={"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:9000"},
        )
        self.assertEqual(mismatched_port.status_code, 403)


if __name__ == "__main__":
    unittest.main()
