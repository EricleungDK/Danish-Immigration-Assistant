import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from danish_rag.local_model_transport import (
    FakeTransport,
    FakeTransportScriptError,
    ModelMissingError,
    ModelProtocolError,
    ModelTimeoutError,
    ModelUnreachableError,
    OLLAMA_DETERMINISTIC_CHAT_OPTIONS,
    OllamaTransport,
    OpenAICompatibleTransport,
    UnsupportedOperationError,
)
from danish_rag.privacy_boundary import PrivacyBoundaryError


class _StubServer:
    """Loopback HTTP stub; routes map (method, path) -> handler(body) -> (status, body, headers)."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                stub.requests.append((self.command, self.path, body))
                route = stub.routes.get((self.command, self.path))
                if route is None:
                    status, payload, headers = 404, {"error": "not found"}, {}
                else:
                    status, payload, headers = route(body)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = _handle
            do_POST = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def ok(payload):
    return lambda body: (200, payload, {})


class FakeTransportTests(unittest.TestCase):
    def test_replays_scripted_responses_in_order_and_records_requests(self):
        fake = FakeTransport()
        fake.queue_version("0.30.6")
        fake.queue_chat('{"status": "ok"}')
        fake.queue_embed([[0.1, 0.2]])

        self.assertEqual(fake.version(), "0.30.6")
        result = fake.chat(
            model="m",
            messages=[{"role": "user", "content": "hi"}],
            schema={"type": "object"},
        )
        self.assertEqual(result.content, '{"status": "ok"}')
        self.assertEqual(fake.embed(model="e", texts=["a"]), [[0.1, 0.2]])

        self.assertEqual(
            [request.operation for request in fake.requests],
            ["version", "chat", "embed"],
        )
        self.assertEqual(fake.requests[1].params["schema"], {"type": "object"})
        self.assertEqual(fake.requests[2].params, {"model": "e", "texts": ["a"]})
        self.assertEqual(fake.remaining, 0)

    def test_scripted_errors_are_raised_in_order(self):
        fake = FakeTransport()
        fake.queue_error(ModelUnreachableError("down"), operation="version")
        with self.assertRaises(ModelUnreachableError):
            fake.version()

    def test_out_of_order_or_exhausted_script_fails_loudly(self):
        fake = FakeTransport()
        fake.queue_chat("x")
        with self.assertRaises(FakeTransportScriptError):
            fake.version()
        with self.assertRaises(FakeTransportScriptError):
            FakeTransport().list_models()

    def test_fake_enforces_loopback(self):
        with self.assertRaises(PrivacyBoundaryError):
            FakeTransport(endpoint="http://example.com:11434")


class LoopbackEnforcementTests(unittest.TestCase):
    def test_construction_with_non_loopback_endpoint_fails_for_every_adapter(self):
        for adapter in (OllamaTransport, OpenAICompatibleTransport):
            for endpoint in (
                "http://example.com:11434",
                "http://192.168.1.5:11434",
                "http://127.0.0.1.evil.example:11434",
                "not-a-url",
            ):
                with self.subTest(adapter=adapter.__name__, endpoint=endpoint):
                    with self.assertRaises(PrivacyBoundaryError):
                        adapter(endpoint)

    def test_calling_after_endpoint_is_changed_to_non_loopback_fails(self):
        transport = OllamaTransport("http://127.0.0.1:1")
        transport.endpoint = "http://example.com:11434"
        for call in (
            transport.version,
            transport.list_models,
            lambda: transport.show_model("m"),
            lambda: transport.chat(model="m", messages=[]),
            lambda: transport.embed(model="m", texts=["a"]),
        ):
            with self.assertRaises(PrivacyBoundaryError):
                call()
        compatible = OpenAICompatibleTransport("http://localhost:1")
        compatible.endpoint = "http://example.com"
        with self.assertRaises(PrivacyBoundaryError):
            compatible.list_models()
        with self.assertRaises(PrivacyBoundaryError):
            compatible.chat(model="m", messages=[])

    def test_redirect_off_loopback_is_not_followed(self):
        routes = {
            ("GET", "/api/version"): lambda body: (
                302,
                {},
                {"Location": "http://example.com/api/version"},
            )
        }
        with _StubServer(routes) as server:
            with self.assertRaises(ModelProtocolError):
                OllamaTransport(server.endpoint).version()
            self.assertEqual(len(server.requests), 1)


class OllamaTransportTests(unittest.TestCase):
    def test_chat_with_schema_sends_deterministic_options_and_disables_thinking(self):
        routes = {
            ("POST", "/api/chat"): ok({"message": {"content": '{"a": 1}'}}),
        }
        with _StubServer(routes) as server:
            result = OllamaTransport(server.endpoint).chat(
                model="gemma4:12b",
                messages=[{"role": "user", "content": "hi"}],
                schema={"type": "object"},
            )
        self.assertEqual(result.content, '{"a": 1}')
        _, _, body = server.requests[0]
        self.assertEqual(body["model"], "gemma4:12b")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])
        self.assertIs(body["stream"], False)
        self.assertIs(body["think"], False)
        self.assertEqual(body["format"], {"type": "object"})
        self.assertEqual(body["options"], {"temperature": 0, "seed": 0})
        self.assertEqual(dict(OLLAMA_DETERMINISTIC_CHAT_OPTIONS), {"temperature": 0, "seed": 0})

    def test_chat_without_schema_omits_format_and_context_option_is_added(self):
        routes = {("POST", "/api/chat"): ok({"message": {"content": "hello"}})}
        with _StubServer(routes) as server:
            OllamaTransport(server.endpoint).chat(
                model="m", messages=[], context_tokens=8192
            )
        body = server.requests[0][2]
        self.assertNotIn("format", body)
        self.assertEqual(body["options"], {"temperature": 0, "seed": 0, "num_ctx": 8192})
        self.assertIs(body["think"], False)

    def test_chat_exposes_done_reason(self):
        routes = {
            ("POST", "/api/chat"): ok(
                {"message": {"content": "x"}, "done_reason": "length"}
            )
        }
        with _StubServer(routes) as server:
            result = OllamaTransport(server.endpoint).chat(model="m", messages=[])
        self.assertEqual(result.done_reason, "length")

    def test_chat_without_message_content_is_a_protocol_error(self):
        routes = {("POST", "/api/chat"): ok({"message": {}})}
        with _StubServer(routes) as server:
            with self.assertRaisesRegex(ModelProtocolError, "message.content"):
                OllamaTransport(server.endpoint).chat(model="m", messages=[])

    def test_embed_posts_texts_and_returns_vectors(self):
        routes = {("POST", "/api/embed"): ok({"embeddings": [[1, 2], [3, 4]]})}
        with _StubServer(routes) as server:
            vectors = OllamaTransport(server.endpoint).embed(
                model="embeddinggemma", texts=["a", "b"]
            )
        self.assertEqual(vectors, [[1, 2], [3, 4]])
        self.assertEqual(
            server.requests[0][2], {"model": "embeddinggemma", "input": ["a", "b"]}
        )

    def test_embed_count_mismatch_or_missing_vectors_is_a_protocol_error(self):
        for payload in ({"embeddings": [[1]]}, {}, {"embeddings": "x"}):
            with self.subTest(payload=payload):
                routes = {("POST", "/api/embed"): ok(payload)}
                with _StubServer(routes) as server:
                    with self.assertRaises(ModelProtocolError):
                        OllamaTransport(server.endpoint).embed(
                            model="m", texts=["a", "b"]
                        )

    def test_embed_accepts_legacy_single_embedding_key(self):
        routes = {("POST", "/api/embed"): ok({"embedding": [0.5]})}
        with _StubServer(routes) as server:
            self.assertEqual(
                OllamaTransport(server.endpoint).embed(model="m", texts=["a"]),
                [[0.5]],
            )

    def test_list_models_returns_installed_entries(self):
        routes = {
            ("GET", "/api/tags"): ok(
                {"models": [{"name": "gemma4:12b", "digest": "d"}, {"model": "x:1"}]}
            )
        }
        with _StubServer(routes) as server:
            models = OllamaTransport(server.endpoint).list_models()
        self.assertEqual([m["name"] for m in models], ["gemma4:12b", "x:1"])
        self.assertEqual(models[0]["digest"], "d")

    def test_version_returns_string(self):
        routes = {("GET", "/api/version"): ok({"version": "0.30.6"})}
        with _StubServer(routes) as server:
            self.assertEqual(OllamaTransport(server.endpoint).version(), "0.30.6")

    def test_show_model_returns_payload_and_missing_model_is_typed(self):
        routes = {
            ("POST", "/api/show"): lambda body: (
                (200, {"details": {"family": "gemma4"}}, {})
                if body["model"] == "here"
                else (404, {"error": "model not found"}, {})
            )
        }
        with _StubServer(routes) as server:
            transport = OllamaTransport(server.endpoint)
            self.assertEqual(transport.show_model("here")["details"]["family"], "gemma4")
            with self.assertRaises(ModelMissingError):
                transport.show_model("gone")

    def test_chat_and_embed_404_mean_model_missing(self):
        routes = {
            ("POST", "/api/chat"): lambda body: (404, {"error": "model not found"}, {}),
            ("POST", "/api/embed"): lambda body: (404, {"error": "model not found"}, {}),
        }
        with _StubServer(routes) as server:
            transport = OllamaTransport(server.endpoint)
            with self.assertRaises(ModelMissingError):
                transport.chat(model="m", messages=[])
            with self.assertRaises(ModelMissingError):
                transport.embed(model="m", texts=["a"])

    def test_unreachable_service_is_typed(self):
        with _StubServer({}) as server:
            endpoint = server.endpoint
        with self.assertRaises(ModelUnreachableError):
            OllamaTransport(endpoint, timeout_seconds=2).version()

    def test_slow_service_raises_timeout(self):
        def slow(body):
            time.sleep(0.6)
            return 200, {"version": "1"}, {}

        with _StubServer({("GET", "/api/version"): slow}) as server:
            with self.assertRaises(ModelTimeoutError):
                OllamaTransport(server.endpoint, timeout_seconds=0.1).version()

    def test_http_errors_and_bad_json_are_protocol_errors(self):
        routes = {
            ("GET", "/api/version"): lambda body: (500, {"error": "boom"}, {}),
            ("GET", "/api/tags"): lambda body: (200, b"not json", {}),
            ("POST", "/api/show"): lambda body: (200, [1], {}),
        }
        with _StubServer(routes) as server:
            transport = OllamaTransport(server.endpoint)
            with self.assertRaisesRegex(ModelProtocolError, "HTTP 500"):
                transport.version()
            with self.assertRaisesRegex(ModelProtocolError, "no usable JSON"):
                transport.list_models()
            with self.assertRaisesRegex(ModelProtocolError, "JSON object"):
                transport.show_model("m")


class OpenAICompatibleTransportTests(unittest.TestCase):
    def test_chat_with_schema_uses_json_schema_response_format(self):
        routes = {
            ("POST", "/v1/chat/completions"): ok(
                {"choices": [{"message": {"content": '{"status": "ok"}'}}]}
            )
        }
        with _StubServer(routes) as server:
            result = OpenAICompatibleTransport(server.endpoint).chat(
                model="m",
                messages=[{"role": "user", "content": "hi"}],
                schema={"type": "object"},
            )
        self.assertEqual(result.content, '{"status": "ok"}')
        body = server.requests[0][2]
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertEqual(body["response_format"]["json_schema"]["schema"], {"type": "object"})
        self.assertEqual(body["temperature"], 0)

    def test_chat_without_content_is_a_protocol_error(self):
        routes = {("POST", "/v1/chat/completions"): ok({"choices": []})}
        with _StubServer(routes) as server:
            with self.assertRaises(ModelProtocolError):
                OpenAICompatibleTransport(server.endpoint).chat(model="m", messages=[])

    def test_list_models_normalizes_ids_to_names(self):
        routes = {("GET", "/v1/models"): ok({"data": [{"id": "a"}, {"id": "b"}]})}
        with _StubServer(routes) as server:
            models = OpenAICompatibleTransport(server.endpoint).list_models()
        self.assertEqual([m["name"] for m in models], ["a", "b"])

    def test_embed_is_explicitly_unsupported_without_any_request(self):
        with _StubServer({}) as server:
            with self.assertRaisesRegex(UnsupportedOperationError, "embed"):
                OpenAICompatibleTransport(server.endpoint).embed(model="m", texts=["a"])
            self.assertEqual(server.requests, [])

    def test_version_and_show_are_unsupported(self):
        transport = OpenAICompatibleTransport("http://127.0.0.1:1")
        with self.assertRaises(UnsupportedOperationError):
            transport.version()
        with self.assertRaises(UnsupportedOperationError):
            transport.show_model("m")

    def test_errors_are_typed(self):
        routes = {
            ("POST", "/v1/chat/completions"): lambda body: (404, {"error": "no"}, {}),
        }
        with _StubServer(routes) as server:
            with self.assertRaises(ModelMissingError):
                OpenAICompatibleTransport(server.endpoint).chat(model="m", messages=[])
            endpoint = server.endpoint
        with self.assertRaises(ModelUnreachableError):
            OpenAICompatibleTransport(endpoint).list_models()


class OllamaContractTests(unittest.TestCase):
    def test_contract_module_reexports_the_adapter_owned_options(self):
        from danish_rag import ollama_contract

        self.assertIs(
            ollama_contract.OLLAMA_DETERMINISTIC_CHAT_OPTIONS,
            OLLAMA_DETERMINISTIC_CHAT_OPTIONS,
        )


if __name__ == "__main__":
    unittest.main()
