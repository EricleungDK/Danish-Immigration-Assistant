import json
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from danish_rag.local_model_transport import (
    FakeTransport,
    ModelProtocolError,
    OllamaTransport,
    OpenAICompatibleTransport,
)
from danish_rag.provider_setup import (
    ProviderCapabilityTester,
    ProviderConfiguration,
    ProviderModelDiscoverer,
)


@contextmanager
def stub_server(routes):
    """Loopback stub: routes maps path -> (status, raw bytes body)."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _serve(self):
            length = int(self.headers.get("Content-Length", 0))
            if length:
                self.rfile.read(length)
            status, body = routes[self.path]
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _serve
        do_POST = _serve

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def js(value):
    return (200, json.dumps(value).encode("utf-8"))


def discover(provider_id, endpoint):
    return ProviderModelDiscoverer()(
        ProviderConfiguration(provider_id=provider_id, endpoint=endpoint, model="")
    )


class TransportMetadataTests(unittest.TestCase):
    def test_model_listing_is_lenient_and_keeps_the_raw_payload(self):
        with stub_server({"/v1/models": js({"object": "list", "data": "nope"})}) as endpoint:
            listing = OpenAICompatibleTransport(endpoint).model_listing()
        self.assertEqual(listing.models, [])
        self.assertEqual(listing.raw["object"], "list")

    def test_strict_list_models_still_rejects_a_missing_models_field(self):
        with stub_server({"/api/tags": js({})}) as endpoint:
            with self.assertRaises(ModelProtocolError):
                OllamaTransport(endpoint).list_models()
            self.assertEqual(OllamaTransport(endpoint).model_listing().models, [])

    def test_embed_result_exposes_provider_durations(self):
        body = {"embeddings": [[0.5, 0.25]], "load_duration": 81000000, "total_duration": 90000000}
        with stub_server({"/api/embed": js(body)}) as endpoint:
            result = OllamaTransport(endpoint).embed_result(model="m", texts=["a"])
        self.assertEqual(result.vectors, [[0.5, 0.25]])
        self.assertEqual(result.raw["load_duration"], 81000000)

    def test_fake_transport_serves_listing_and_embed_result(self):
        fake = FakeTransport()
        fake.queue_models([{"name": "a"}], raw={"object": "list"})
        fake.queue_embed([[1.0]], raw={"load_duration": 5})
        self.assertEqual(fake.model_listing().raw, {"object": "list"})
        result = fake.embed_result(model="m", texts=["a"])
        self.assertEqual((result.vectors, result.raw), ([[1.0]], {"load_duration": 5}))


class MalformedListingTests(unittest.TestCase):
    def test_ollama_discovery_missing_or_non_list_models_is_no_compatible_models(self):
        for body in ({}, {"models": "x"}, {"models": None}):
            with self.subTest(body=body), stub_server({"/api/tags": js(body)}) as endpoint:
                self.assertEqual(discover("ollama", endpoint).reason, "no_compatible_models")

    def test_openai_discovery_missing_or_non_list_data_is_no_compatible_models(self):
        for body in ({}, {"data": "x"}):
            with self.subTest(body=body), stub_server({"/v1/models": js(body)}) as endpoint:
                self.assertEqual(
                    discover("openai_compatible", endpoint).reason, "no_compatible_models"
                )

    def test_garbage_discovery_bodies_stay_unreachable(self):
        for provider_id, path in (("ollama", "/api/tags"), ("openai_compatible", "/v1/models")):
            for raw in (b"not json", b"[1, 2]"):
                with self.subTest(path=path, raw=raw), stub_server(
                    {path: (200, raw)}
                ) as endpoint:
                    self.assertEqual(discover(provider_id, endpoint).reason, "service_unreachable")

    def test_openai_capability_tolerates_missing_data_and_keeps_provider_version(self):
        chat = js({"choices": [{"message": {"content": json.dumps({"status": "ok"})}}]})
        for models_body, version in (
            ({"object": "list"}, "list"),
            ({"object": "list", "data": "x"}, "list"),
            ({}, "openai-compatible"),
        ):
            routes = {"/v1/models": js(models_body), "/v1/chat/completions": chat}
            with self.subTest(body=models_body), stub_server(routes) as endpoint:
                result = ProviderCapabilityTester()(
                    ProviderConfiguration(
                        provider_id="openai_compatible", endpoint=endpoint, model="chat-a"
                    )
                )
                self.assertTrue(result.ok, result)
                self.assertEqual(result.provider_version, version)

    def test_openai_capability_garbage_models_body_is_unreachable(self):
        for raw in (b"not json", b"[1]"):
            with self.subTest(raw=raw), stub_server({"/v1/models": (200, raw)}) as endpoint:
                result = ProviderCapabilityTester()(
                    ProviderConfiguration(
                        provider_id="openai_compatible", endpoint=endpoint, model="chat-a"
                    )
                )
                self.assertEqual(result.reason, "service_unreachable")


if __name__ == "__main__":
    unittest.main()
