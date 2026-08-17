import copy
import hashlib
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from danish_rag.answer_pipeline import AnswerService
from danish_rag.knowledge_release import (
    KnowledgeReleaseError,
    install_knowledge_release,
    load_active_release,
)
from danish_rag.production_knowledge_release import (
    build_reviewed_candidate_release,
    install_and_verify_candidate,
    verify_candidate_rollback_matrix,
)
from danish_rag.provider_setup import ProviderConfiguration
from danish_rag.retrieval import HybridRetriever
from danish_rag.source_registry import (
    SourceRegistryError,
    assess_source_registry_qualification,
    load_source_registry,
    validate_source_registry_against_release,
)
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture
from tests.release_trust_fixture import create_test_release_trust_fixture

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
        review_dir: Path = REVIEW_DIR,
        source_registry_version: str = "sr-2026-08-17.1",
        release_id: str = "kr-2026-08-17.1",
        created_at_utc: str = "2026-08-17T12:00:00Z",
        next_review_due_utc: str = "2026-10-26T20:55:12Z",
        signing_private_key_path: Path | None = None,
    ):
        return build_reviewed_candidate_release(
            review_dir=review_dir,
            registry_path=self.root / name / f"{source_registry_version}.json",
            release_dir=self.root / name / release_id,
            source_registry_version=source_registry_version,
            release_id=release_id,
            created_at_utc=created_at_utc,
            next_review_due_utc=next_review_due_utc,
            release_operator_ids=("ericleungDK",),
            release_approver_ids=("ericleungDK",),
            recovery_owner_ids=("ericleungDK",),
            signing_private_key_path=(
                signing_private_key_path or self.release_trust.signing_private_key_path
            ),
            trust_root_path=self.release_trust.trust_root_path,
        )

    def copy_review_dir(self, name: str) -> Path:
        destination = self.root / name
        shutil.copytree(REVIEW_DIR, destination)
        return destination

    @staticmethod
    def load_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def write_json(path: Path, value: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def rebind_review_files(
        self,
        review_dir: Path,
        *,
        bundle_changed: bool = False,
        decisions_changed: bool = False,
    ) -> None:
        completed_path = review_dir / "completed-review.json"
        decisions_path = review_dir / "human-decisions.json"
        completed = self.load_json(completed_path)
        decisions = self.load_json(decisions_path)
        if bundle_changed:
            bundle_path = review_dir / "review-bundle.json"
            bundle_sha256 = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
            completed["machine_review_manifest_sha256"] = bundle_sha256
            decisions["review_bundle"]["sha256"] = bundle_sha256
            decisions_changed = True
        if decisions_changed:
            self.write_json(decisions_path, decisions)
            completed["human_decisions_sha256"] = hashlib.sha256(
                decisions_path.read_bytes()
            ).hexdigest()
        self.write_json(completed_path, completed)

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
            registry["release_governance"],
            {
                "recorded_at_utc": "2026-08-17T12:00:00Z",
                "recovery_owner_ids": ["ericleungDK"],
                "release_approver_ids": ["ericleungDK"],
                "release_operator_ids": ["ericleungDK"],
            },
        )
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

    def test_builder_rejects_flattened_review_drift_from_human_decisions(self):
        review_dir = self.copy_review_dir("flattened-drift-review")
        completed_path = review_dir / "completed-review.json"
        completed = self.load_json(completed_path)
        completed["sources"][0]["human_review"]["materiality"] = "non-material"
        completed["sources"][0]["human_review"]["staffing"] = (
            "single-reviewer-non-material"
        )
        self.write_json(completed_path, completed)

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "differs from bound human decisions",
        ):
            self.build_candidate("flattened-drift", review_dir=review_dir)

    def test_builder_rejects_failed_http_monitoring_evidence(self):
        review_dir = self.copy_review_dir("failed-fetch-review")
        bundle_path = review_dir / "review-bundle.json"
        bundle = self.load_json(bundle_path)
        bundle["sources"][0]["retrieval"]["http_status"] = 404
        self.write_json(bundle_path, bundle)
        self.rebind_review_files(review_dir, bundle_changed=True)

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "successful reviewed retrieval",
        ):
            self.build_candidate("failed-fetch", review_dir=review_dir)

    def test_separated_second_reviewer_evidence_is_preserved(self):
        review_dir = self.copy_review_dir("separated-review")
        decisions_path = review_dir / "human-decisions.json"
        completed_path = review_dir / "completed-review.json"
        decisions = self.load_json(decisions_path)
        completed = self.load_json(completed_path)
        for record in (
            decisions["source_decisions"][0],
            completed["sources"][0],
        ):
            record["human_review"]["staffing"] = "separated-human-review"
            record["human_review"]["second_reviewer_ids"] = ["second-reviewer"]
        self.write_json(decisions_path, decisions)
        completed["human_decisions_sha256"] = hashlib.sha256(
            decisions_path.read_bytes()
        ).hexdigest()
        self.write_json(completed_path, completed)

        candidate = self.build_candidate("separated", review_dir=review_dir)

        self.assertEqual(
            candidate.registry["sources"][0]["review_evidence"]["second_reviewer_ids"],
            ["second-reviewer"],
        )

    def test_failed_build_removes_claimed_candidate_outputs_for_retry(self):
        registry_path = self.root / "retry" / "sr-2026-08-17.1.json"
        release_dir = self.root / "retry" / "kr-2026-08-17.1"

        with self.assertRaises(KnowledgeReleaseError):
            build_reviewed_candidate_release(
                review_dir=REVIEW_DIR,
                registry_path=registry_path,
                release_dir=release_dir,
                source_registry_version="sr-2026-08-17.1",
                release_id="kr-2026-08-17.1",
                created_at_utc="2026-08-17T12:00:00Z",
                next_review_due_utc="2026-10-26T20:55:12Z",
                release_operator_ids=("ericleungDK",),
                release_approver_ids=("ericleungDK",),
                recovery_owner_ids=("ericleungDK",),
                signing_private_key_path=self.root / "missing-private-key.pem",
                trust_root_path=self.release_trust.trust_root_path,
            )

        self.assertFalse(registry_path.exists())
        self.assertFalse(release_dir.exists())

    def test_builder_rejects_invalid_release_and_review_timestamps(self):
        for field, value in (
            ("created_at_utc", "not-a-date"),
            ("next_review_due_utc", "not-a-date"),
        ):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(
                    KnowledgeReleaseError,
                    "UTC timestamp",
                ),
            ):
                self.build_candidate(
                    f"invalid-{field}",
                    **{field: value},
                )

    def test_builder_rejects_missing_extraction_schema_version(self):
        review_dir = self.copy_review_dir("missing-schema-review")
        bundle_path = review_dir / "review-bundle.json"
        bundle = self.load_json(bundle_path)
        bundle["sources"][0]["normalized_extraction"]["extraction_schema_version"] = (
            None
        )
        self.write_json(bundle_path, bundle)
        self.rebind_review_files(review_dir, bundle_changed=True)

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "extraction schema version",
        ):
            self.build_candidate("missing-schema", review_dir=review_dir)

    def test_retrieval_qualification_failure_keeps_prior_release_active(self):
        prior = self.build_candidate(
            "qualification-prior",
            source_registry_version="sr-2026-08-16.1",
            release_id="kr-2026-08-16.1",
        )
        candidate = self.build_candidate("qualification-candidate")
        data_dir = self.root / "qualification-data"
        provider = DeterministicEmbeddingProviderFixture()
        install_knowledge_release(
            data_dir,
            release_dir=prior.release_dir,
            embedding_provider=provider,
            trust_root_path=self.release_trust.trust_root_path,
        )

        with self.assertRaisesRegex(
            KnowledgeReleaseError,
            "retrieval qualification failed",
        ):
            install_and_verify_candidate(
                data_dir=data_dir,
                release_dir=candidate.release_dir,
                embedding_provider=provider,
                trust_root_path=self.release_trust.trust_root_path,
                queries=(
                    {
                        "id": "impossible-source",
                        "query_text": "What Danish language test is required?",
                        "required_source_ids": ["not-in-candidate"],
                        "forbidden_document_ids": [],
                    },
                ),
            )

        active = load_active_release(
            data_dir,
            trust_root_path=self.release_trust.trust_root_path,
        )
        self.assertEqual(
            active["manifest"]["knowledge_release_id"],
            "kr-2026-08-16.1",
        )

    def test_empty_retrieval_qualification_suite_is_rejected_before_install(self):
        candidate = self.build_candidate("empty-qualification")
        data_dir = self.root / "empty-qualification-data"

        with self.assertRaisesRegex(KnowledgeReleaseError, "must contain queries"):
            install_and_verify_candidate(
                data_dir=data_dir,
                release_dir=candidate.release_dir,
                embedding_provider=DeterministicEmbeddingProviderFixture(),
                trust_root_path=self.release_trust.trust_root_path,
                queries=(),
            )

        self.assertFalse(data_dir.exists())

    def test_concurrent_builds_cannot_share_candidate_output_paths(self):
        registry_path = self.root / "concurrent" / "candidate.json"
        release_dir = self.root / "concurrent" / "candidate-release"
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def build(release_id: str) -> None:
            barrier.wait()
            try:
                build_reviewed_candidate_release(
                    review_dir=REVIEW_DIR,
                    registry_path=registry_path,
                    release_dir=release_dir,
                    source_registry_version=release_id.replace("kr-", "sr-"),
                    release_id=release_id,
                    created_at_utc="2026-08-17T12:00:00Z",
                    next_review_due_utc="2026-10-26T20:55:12Z",
                    release_operator_ids=("ericleungDK",),
                    release_approver_ids=("ericleungDK",),
                    recovery_owner_ids=("ericleungDK",),
                    signing_private_key_path=(
                        self.release_trust.signing_private_key_path
                    ),
                    trust_root_path=self.release_trust.trust_root_path,
                )
            except KnowledgeReleaseError:
                outcomes.append("rejected")
            else:
                outcomes.append("built")

        threads = [
            threading.Thread(target=build, args=(f"kr-2026-08-17.{number}",))
            for number in (1, 2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertEqual(sorted(outcomes), ["built", "rejected"])
        manifest = self.load_json(release_dir / "manifest.json")
        registry = self.load_json(registry_path)
        self.assertEqual(
            manifest["knowledge_release_id"],
            registry["knowledge_release_id"],
        )


if __name__ == "__main__":
    unittest.main()
