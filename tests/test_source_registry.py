import copy
import json
import tempfile
import unittest
from pathlib import Path

from danish_rag.source_registry import (
    SourceRegistryError,
    assess_source_registry_qualification,
    load_source_registry,
    validate_source_registry_against_release,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data" / "source_registry" / "sr-2026-07-06.1.json"
RELEASE_DIR = ROOT / "data" / "knowledge_releases" / "kr-2026-07-06.1"


class SourceRegistryTests(unittest.TestCase):
    def test_active_fixture_registry_is_machine_readable_and_matches_release(self) -> None:
        registry = load_source_registry(REGISTRY_PATH)

        result = validate_source_registry_against_release(registry, RELEASE_DIR)

        self.assertEqual(registry["source_registry_version"], "sr-2026-07-06.1")
        self.assertEqual(result["knowledge_release_id"], "kr-2026-07-06.1")
        self.assertEqual(result["source_count"], 5)
        self.assertEqual(result["fixture_document_count"], 5)
        self.assertEqual(result["production_human_reviewed_source_count"], 0)

    def test_fixture_registry_blocks_production_qualification_without_inventing_review(
        self,
    ) -> None:
        registry = load_source_registry(REGISTRY_PATH)

        result = assess_source_registry_qualification(registry)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["production_release_eligible"])
        self.assertEqual(result["production_human_reviewed_source_count"], 0)
        self.assertEqual(
            set(result["reason_codes"]),
            {
                "fixture-governance-evidence-only",
                "official-source-snapshots-not-recorded",
                "production-human-source-review-not-recorded",
                "project-authored-fixture-content",
                "source-curation-not-recorded",
                "source-monitoring-evidence-not-recorded",
            },
        )
        for source in registry["sources"]:
            self.assertEqual(source["registry_state"], "discovered")
            self.assertEqual(source["content_origin"], "project-authored-fixture")
            self.assertFalse(source["production_release_eligible"])
            self.assertEqual(source["curation_evidence"]["status"], "not-recorded")
            self.assertEqual(source["curation_evidence"]["curator_ids"], [])
            self.assertEqual(source["monitoring_evidence"]["status"], "not-recorded")
            self.assertEqual(source["monitoring_evidence"]["owner_ids"], [])
            self.assertEqual(source["review_evidence"]["status"], "not-recorded")
            self.assertEqual(source["review_evidence"]["reviewer_ids"], [])
            self.assertIsNone(source["review_evidence"]["reviewed_at_utc"])

    def test_registry_cannot_claim_production_eligibility_without_human_review_evidence(
        self,
    ) -> None:
        registry = load_source_registry(REGISTRY_PATH)
        invalid = copy.deepcopy(registry)
        source = invalid["sources"][0]
        source["production_release_eligible"] = True
        source["registry_state"] = "approved-current"
        source["content_origin"] = "official-source-normalized-extract"
        source["curation_evidence"] = {
            "status": "completed",
            "curator_ids": ["maintainer-curator-001"],
            "admitted_at_utc": "2026-07-06T10:00:00Z",
            "scope_rationale": "Official source within the documented MVP topic boundary.",
        }
        source["monitoring_evidence"] = {
            "status": "recorded",
            "owner_ids": ["maintainer-monitor-001"],
            "last_fetched_at_utc": "2026-07-06T11:00:00Z",
            "final_url": source["official_url"],
            "http_status": 200,
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-registry.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")

            with self.assertRaisesRegex(
                SourceRegistryError,
                "production eligibility requires completed human review",
            ):
                load_source_registry(path)

    def test_registry_rejects_fixture_reviewer_label_as_production_human_review(self) -> None:
        registry = load_source_registry(REGISTRY_PATH)
        invalid = copy.deepcopy(registry)
        source = invalid["sources"][0]
        source["registry_state"] = "approved-current"
        source["review_evidence"] = {
            "status": "completed",
            "assessment_method": "human-source-and-normalized-extraction-review",
            "reviewed_at_utc": "2026-07-06T12:00:00Z",
            "reviewer_ids": ["mvp-fixture-reviewer"],
            "official_source_snapshot_sha256": "0" * 64,
            "normalized_extraction_sha256": "1" * 64,
            "decision": "approved-current",
            "materiality": "non-material",
            "notes": "Synthetic invalid-record test.",
            "interpretation_risks": [],
            "second_reviewer_ids": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-registry.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")

            with self.assertRaisesRegex(SourceRegistryError, "placeholder reviewer"):
                load_source_registry(path)

    def test_material_source_change_requires_second_human_reviewer_evidence(self) -> None:
        registry = load_source_registry(REGISTRY_PATH)
        invalid = copy.deepcopy(registry)
        source = invalid["sources"][0]
        source["registry_state"] = "approved-current"
        source["review_evidence"] = {
            "status": "completed",
            "assessment_method": "human-source-and-normalized-extraction-review",
            "reviewed_at_utc": "2026-07-06T12:00:00Z",
            "reviewer_ids": ["maintainer-reviewer-001"],
            "official_source_snapshot_sha256": "0" * 64,
            "normalized_extraction_sha256": "1" * 64,
            "decision": "approved-current",
            "materiality": "material",
            "notes": "Synthetic material-change validation test.",
            "interpretation_risks": [],
            "second_reviewer_ids": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-registry.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")

            with self.assertRaisesRegex(SourceRegistryError, "second human reviewer"):
                load_source_registry(path)

    def test_release_cross_check_detects_fixture_projection_drift(self) -> None:
        registry = load_source_registry(REGISTRY_PATH)
        drifted = copy.deepcopy(registry)
        drifted["sources"][0]["fixture_projection"][
            "manifest_source_content_sha256"
        ] = "f" * 64

        with self.assertRaisesRegex(SourceRegistryError, "fixture projection"):
            validate_source_registry_against_release(drifted, RELEASE_DIR)


if __name__ == "__main__":
    unittest.main()
