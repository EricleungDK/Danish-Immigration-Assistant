import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from danish_rag.answer_pipeline import AnswerService
from danish_rag.production_knowledge_release import (
    build_reviewed_candidate_release,
    install_and_verify_candidate,
    verify_candidate_rollback_matrix,
)
from danish_rag.source_registry import (
    SourceRegistryError,
    assess_source_registry_qualification,
    load_source_registry,
    validate_source_registry_against_release,
)
from danish_rag.provider_setup import ProviderConfiguration
from danish_rag.retrieval import HybridRetriever
from tests.release_trust_fixture import create_test_release_trust_fixture
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture


ROOT = Path(__file__).resolve().parents[1]
REVIEW_DIR = ROOT / "data" / "source_reviews" / "issue-46"


class FirstChunkAnswerGenerator:
    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "summary": "Reviewed official evidence found.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": evidence[0]["content"],
                    "citation_ids": [evidence[0]["citation_id"]],
                }
            ],
        }


class Issue50ProductionReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.release_trust = create_test_release_trust_fixture(
            self.root / "test-only-release-trust"
        )

    def build_candidate(
        self,
        name: str,
        *,
        source_registry_version: str = "sr-2026-08-17.1",
        release_id: str = "kr-2026-08-17.1",
    ):
        return build_reviewed_candidate_release(
            review_dir=REVIEW_DIR,
            registry_path=self.root / name / f"{source_registry_version}.json",
            release_dir=self.root / name / release_id,
            source_registry_version=source_registry_version,
            release_id=release_id,
            created_at_utc="2026-08-17T12:00:00Z",
            next_review_due_utc="2026-10-26T20:55:12Z",
            signing_private_key_path=self.release_trust.signing_private_key_path,
            trust_root_path=self.release_trust.trust_root_path,
        )

    def test_completed_review_builds_a_deterministic_qualified_chunk_release(self):
        first = self.build_candidate("first")
        second = self.build_candidate("second")

        registry = load_source_registry(first.registry_path)
        qualification = assess_source_registry_qualification(registry)
        cross_check = validate_source_registry_against_release(
            registry,
            first.release_dir,
        )
        manifest = json.loads(
            (first.release_dir / "manifest.json").read_text(encoding="utf-8")
        )
        chunks = json.loads(
            (first.release_dir / "corpus" / "documents.json").read_text(
                encoding="utf-8"
            )
        )
        repeated_chunks = json.loads(
            (second.release_dir / "corpus" / "documents.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(qualification["status"], "qualified")
        self.assertTrue(qualification["production_release_eligible"])
        self.assertEqual(qualification["production_human_reviewed_source_count"], 5)
        self.assertEqual(cross_check["source_count"], 5)
        self.assertEqual(cross_check["fixture_document_count"], 0)
        self.assertEqual(manifest["corpus_schema_version"], "2.0")
        self.assertEqual(manifest["content_unit_schema_version"], "semantic-chunk-v1")
        self.assertEqual(len(manifest["sources"]), 5)
        self.assertGreater(len(chunks), 5)
        self.assertEqual(
            [chunk["chunk_id"] for chunk in chunks],
            [chunk["chunk_id"] for chunk in repeated_chunks],
        )
        self.assertEqual(
            {chunk["source_id"] for chunk in chunks},
            {source["source_id"] for source in registry["sources"]},
        )

        completed_review = json.loads(
            (REVIEW_DIR / "completed-review.json").read_text(encoding="utf-8")
        )
        completed_by_id = {
            source["source_id"]: source for source in completed_review["sources"]
        }
        manifest_by_id = {source["source_id"]: source for source in manifest["sources"]}
        for source in registry["sources"]:
            source_id = source["source_id"]
            reviewed = completed_by_id[source_id]
            released = manifest_by_id[source_id]
            extraction_path = (
                REVIEW_DIR / source["review_evidence"]["normalized_extraction_path"]
            )
            self.assertEqual(
                source["curation_evidence"]["curator_ids"], ["ericleungDK"]
            )
            self.assertEqual(
                source["monitoring_evidence"]["owner_ids"], ["ericleungDK"]
            )
            self.assertEqual(source["review_evidence"]["reviewer_ids"], ["ericleungDK"])
            self.assertEqual(
                source["review_evidence"]["materiality"],
                reviewed["human_review"]["materiality"],
            )
            self.assertEqual(
                released["official_url"],
                reviewed["url_resolution"]["approved_url"],
            )
            self.assertEqual(
                released["normalized_extraction_sha256"],
                hashlib.sha256(extraction_path.read_bytes()).hexdigest(),
            )
            if source["review_evidence"]["materiality"] == "material":
                self.assertEqual(
                    source["review_evidence"]["staffing"],
                    "mvp-single-maintainer-fallback",
                )
                self.assertTrue(
                    source["review_evidence"]["single_maintainer_fallback"]["selected"]
                )

    def test_installed_candidate_is_indexed_and_has_zero_retrieval_violations(self):
        candidate = self.build_candidate("installed")

        report = install_and_verify_candidate(
            data_dir=self.root / "local-data",
            release_dir=candidate.release_dir,
            embedding_provider=DeterministicEmbeddingProviderFixture(),
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertEqual(report["knowledge_release_id"], "kr-2026-08-17.1")
        self.assertEqual(report["indexed_unit"], "semantic-chunk")
        self.assertGreater(report["indexed_chunk_count"], 5)
        self.assertEqual(report["summary"]["blocked_source_violations"], 0)
        self.assertEqual(report["summary"]["forbidden_result_violations"], 0)
        self.assertEqual(report["summary"]["required_source_misses"], 0)
        for query in report["queries"]:
            self.assertTrue(query["results"])
            for result in query["results"]:
                self.assertEqual(result["review_state"], "approved-current")
                self.assertEqual(result["source_health"], "healthy")
                self.assertEqual(len(result["source_content_sha256"]), 64)
                self.assertEqual(len(result["normalized_extraction_sha256"]), 64)
                self.assertEqual(len(result["normalized_document_sha256"]), 64)
                self.assertEqual(result["knowledge_release_id"], "kr-2026-08-17.1")

        answer = AnswerService(
            retriever=HybridRetriever.from_data_dir(
                self.root / "local-data",
                embedding_provider=DeterministicEmbeddingProviderFixture(),
                trust_root_path=self.release_trust.trust_root_path,
            ),
            generator=FirstChunkAnswerGenerator(),
        ).answer(
            "What Danish language test is required for permanent residence?",
            ProviderConfiguration(
                provider_id="openai_compatible",
                endpoint="http://127.0.0.1:1234",
                model="issue-50-fixture-model",
                provider_version="fixture",
                model_identity={"id": "issue-50-fixture-model"},
                capabilities=["generation"],
                validated_at_utc="2026-08-17T12:30:00Z",
            ),
        )
        citation = answer.answer["citations"][0]
        self.assertEqual(len(citation["normalized_extraction_sha256"]), 64)
        self.assertEqual(citation["knowledge_release_id"], "kr-2026-08-17.1")

    def test_candidate_failures_keep_the_prior_reviewed_release_active(self):
        prior = self.build_candidate(
            "prior",
            source_registry_version="sr-2026-08-16.1",
            release_id="kr-2026-08-16.1",
        )
        candidate = self.build_candidate("candidate")

        report = verify_candidate_rollback_matrix(
            workspace=self.root / "rollback-matrix",
            prior_release_dir=prior.release_dir,
            candidate_release_dir=candidate.release_dir,
            embedding_provider=DeterministicEmbeddingProviderFixture(),
            trust_root_path=self.release_trust.trust_root_path,
        )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(
            [case["phase"] for case in report["cases"]],
            ["verification", "extraction", "embedding", "indexing", "activation"],
        )
        self.assertTrue(all(case["prior_release_retained"] for case in report["cases"]))
        self.assertTrue(
            all(case["prior_release_queryable"] for case in report["cases"])
        )

    def test_registry_cross_check_rejects_review_evidence_not_bound_to_release(self):
        candidate = self.build_candidate("drifted-registry")
        drifted = copy.deepcopy(candidate.registry)
        drifted["sources"][0]["review_evidence"]["normalized_extraction_sha256"] = (
            "f" * 64
        )

        with self.assertRaisesRegex(
            SourceRegistryError,
            "normalized extraction differs",
        ):
            validate_source_registry_against_release(
                drifted,
                candidate.release_dir,
            )


if __name__ == "__main__":
    unittest.main()
