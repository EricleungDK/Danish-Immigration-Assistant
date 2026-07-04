import os
import tempfile
import unittest
from pathlib import Path

from danish_rag.retrieval_benchmark import (
    DENSE_BENCHMARK_ID,
    DENSE_INDEX_ENGINE,
    DENSE_INDEX_SCHEMA_VERSION,
    load_benchmark_inputs,
    run_dense_retrieval_benchmark,
)


ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "data" / "retrieval_benchmark" / "corpus-fixtures.json"
DENSE_QUERIES_PATH = ROOT / "data" / "retrieval_benchmark" / "dense-evaluation-queries.json"
POLICY_PATH = ROOT / "config" / "runtime-policy.json"
LIVE_GATE_ENV = "DI_RAG_RUN_LIVE_DENSE_BENCHMARK"


@unittest.skipUnless(
    os.environ.get(LIVE_GATE_ENV) == "1",
    f"set {LIVE_GATE_ENV}=1 to run the live local embedding benchmark gate",
)
class LiveDenseRetrievalBenchmarkGateTests(unittest.TestCase):
    def test_live_dense_benchmark_uses_configured_embedding_stack_and_reusable_index(self):
        fixtures, queries = load_benchmark_inputs(CORPUS_PATH, DENSE_QUERIES_PATH)
        expected_document_ids = [fixture["id"] for fixture in fixtures]
        expected_query_ids = [query["id"] for query in queries]

        with tempfile.TemporaryDirectory(prefix="di-rag-live-dense-benchmark-") as tempdir:
            temp_path = Path(tempdir)
            index_path = temp_path / "dense-index.json"
            output_path = temp_path / "dense-result.json"
            reuse_output_path = temp_path / "dense-result-reuse.json"

            result = run_dense_retrieval_benchmark(
                CORPUS_PATH,
                DENSE_QUERIES_PATH,
                index_path=index_path,
                output_path=output_path,
                policy_path=POLICY_PATH,
            )
            reuse_result = run_dense_retrieval_benchmark(
                CORPUS_PATH,
                DENSE_QUERIES_PATH,
                index_path=index_path,
                output_path=reuse_output_path,
                policy_path=POLICY_PATH,
                rebuild_index=False,
            )

            self.assertTrue(output_path.exists())
            self.assertTrue(index_path.exists())
            self.assertTrue(reuse_output_path.exists())

        self.assertEqual(result["benchmark_id"], DENSE_BENCHMARK_ID)
        self.assertEqual(result["index"]["engine"], DENSE_INDEX_ENGINE)
        self.assertEqual(result["index"]["schema_version"], DENSE_INDEX_SCHEMA_VERSION)
        self.assertEqual(result["index"]["document_count"], len(fixtures))
        self.assertEqual(result["fixture_identity"]["document_ids"], expected_document_ids)
        self.assertEqual(result["fixture_identity"]["query_ids"], expected_query_ids)
        self.assertGreater(result["index"]["vector_dimensions"], 0)
        self.assertEqual(result["summary"]["query_count"], len(queries))
        self.assertEqual(result["summary"]["forbidden_result_violations"], 0)
        self.assertEqual(result["summary"]["blocked_source_violations"], 0)
        self.assertEqual(result["operations"]["embedding_call_count"], len(fixtures) + len(queries))

        self.assertEqual(reuse_result["benchmark_id"], DENSE_BENCHMARK_ID)
        self.assertEqual(reuse_result["index"], result["index"])
        self.assertEqual(reuse_result["fixture_identity"], result["fixture_identity"])
        self.assertEqual(reuse_result["summary"]["query_count"], len(queries))


if __name__ == "__main__":
    unittest.main()
