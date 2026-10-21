import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from danish_rag import retrieval_benchmark
from danish_rag.local_model_transport import (
    FakeTransport,
    ModelMissingError,
    ModelUnreachableError,
)
from danish_rag.retrieval_benchmark import (
    BenchmarkValidationError,
    TransportEmbeddingClient,
    run_dense_retrieval_benchmark,
    run_hybrid_retrieval_comparison,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "data" / "retrieval_benchmark" / "corpus-fixtures.json"
QUERIES_PATH = ROOT / "data" / "retrieval_benchmark" / "evaluation-queries.json"
DENSE_QUERIES_PATH = ROOT / "data" / "retrieval_benchmark" / "dense-evaluation-queries.json"


class _OllamaStub(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"version": "0.30.5"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length))
        if self.path == "/api/show":
            self._send({"model": payload["model"], "details": {"family": "embeddinggemma"}})
        else:
            self.server.embed_inputs.append(payload["input"])
            self._send(
                {
                    "embeddings": [[1.0, 0.5, 0.25] for _ in payload["input"]],
                    "total_duration": 90_000_000,
                    "load_duration": 81_000_000,
                    "prompt_eval_duration": 4_000_000,
                }
            )


class TransportEmbeddingClientTests(unittest.TestCase):
    def test_embed_returns_the_ollama_payload_shape_for_one_text(self):
        transport = FakeTransport()
        transport.queue_embed([[0.1, 0.2]])

        payload = TransportEmbeddingClient(transport).embed("embeddinggemma", "hej")

        self.assertEqual(payload, {"embeddings": [[0.1, 0.2]]})
        self.assertEqual(transport.requests[0].params, {"model": "embeddinggemma", "texts": ["hej"]})

    def test_embed_keeps_provider_duration_fields(self):
        transport = FakeTransport()
        transport.queue_embed([[0.1]], raw={"load_duration": 7, "model": "m"})

        payload = TransportEmbeddingClient(transport).embed("embeddinggemma", "hej")

        self.assertEqual(payload["embeddings"], [[0.1]])
        self.assertEqual(payload["load_duration"], 7)

    def test_show_model_maps_missing_model_to_file_not_found(self):
        transport = FakeTransport()
        transport.queue_error(ModelMissingError("gone"))

        with self.assertRaises(FileNotFoundError):
            TransportEmbeddingClient(transport).show_model("embeddinggemma")

    def test_get_version_wraps_the_transport_version(self):
        transport = FakeTransport()
        transport.queue_version("0.30.5")

        self.assertEqual(TransportEmbeddingClient(transport).get_version(), {"version": "0.30.5"})

    def test_transport_errors_propagate_from_embed(self):
        transport = FakeTransport()
        transport.queue_error(ModelUnreachableError("down"))

        with self.assertRaises(ModelUnreachableError):
            TransportEmbeddingClient(transport).embed("embeddinggemma", "hej")


class BenchmarkLoopbackTests(unittest.TestCase):
    def test_module_has_no_http_client_of_its_own(self):
        source = Path(retrieval_benchmark.__file__).read_text(encoding="utf-8")

        self.assertNotIn("urllib.request", source)
        self.assertNotIn("urlopen", source)

    def test_dense_benchmark_rejects_non_loopback_embedding_endpoint(self):
        with self.assertRaisesRegex(BenchmarkValidationError, "loopback"):
            run_dense_retrieval_benchmark(
                CORPUS_PATH,
                DENSE_QUERIES_PATH,
                embedding_endpoint="http://example.com:11434",
                embedding_model="embeddinggemma",
            )

    def test_hybrid_comparison_rejects_non_loopback_embedding_endpoint(self):
        with self.assertRaisesRegex(BenchmarkValidationError, "loopback"):
            run_hybrid_retrieval_comparison(
                CORPUS_PATH,
                QUERIES_PATH,
                DENSE_QUERIES_PATH,
                embedding_endpoint="http://example.com:11434",
                embedding_model="embeddinggemma",
            )

    def test_dense_benchmark_embeds_through_a_loopback_server(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaStub)
        server.embed_inputs = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tempdir:
                result = run_dense_retrieval_benchmark(
                    CORPUS_PATH,
                    DENSE_QUERIES_PATH,
                    index_path=Path(tempdir) / "index.json",
                    output_path=Path(tempdir) / "result.json",
                    embedding_endpoint=f"http://127.0.0.1:{server.server_port}",
                    embedding_model="embeddinggemma",
                )
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(result["index"]["vector_dimensions"], 3)
        self.assertTrue(result["operations"]["cold_embedding_load_observed"])
        durations = result["operations"]["embedding_service_durations_ms"]
        self.assertEqual(set(durations), {"total_duration", "load_duration", "prompt_eval_duration"})
        self.assertEqual(durations["load_duration"][0], 81.0)
        for query in result["queries"]:
            self.assertEqual(query["embedding_load_duration_ms"], 81.0)
        self.assertTrue(server.embed_inputs)
        self.assertTrue(all(isinstance(item, list) and len(item) == 1 for item in server.embed_inputs))


if __name__ == "__main__":
    unittest.main()
