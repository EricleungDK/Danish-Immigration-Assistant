from __future__ import annotations

import json
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from danish_rag.source_admission import (
    SourceAdmissionPacketError,
    main,
    write_source_admission_packet,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data" / "source_registry" / "sr-2026-07-06.1.json"
RELEASE_DIR = ROOT / "data" / "knowledge_releases" / "kr-2026-07-06.1"


class SourceAdmissionPacketTests(unittest.TestCase):
    def test_packet_binds_discovered_sources_and_keeps_human_fields_blank(self) -> None:
        original_registry = REGISTRY_PATH.read_bytes()
        observations = [
            {
                "source_id": "nyidanmark-permanent-residence-language-requirements",
                "code": "publisher-attribution-differs",
                "summary": (
                    "The current official page attributes publication to "
                    "Udlændingestyrelsen rather than SIRI."
                ),
                "registry_value": "SIRI",
                "observed_value": "Udlændingestyrelsen",
                "reference_url": (
                    "https://www.nyidanmark.dk/da/Du-vil-ans%C3%B8ge/"
                    "Permanent-ophold/Permanent-ophold"
                ),
            }
        ]

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "source-admission-packet.json"
            with patch("urllib.request.urlopen") as urlopen:
                packet = write_source_admission_packet(
                    repo_root=ROOT,
                    registry_path=REGISTRY_PATH,
                    output_path=output_path,
                    generated_at_utc="2026-07-14T18:00:00Z",
                    machine_observations=observations,
                )

            urlopen.assert_not_called()
            self.assertEqual(REGISTRY_PATH.read_bytes(), original_registry)
            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")),
                packet,
            )

        self.assertEqual(packet["schema_version"], "source-admission-packet-v1")
        self.assertEqual(packet["classification"], "sensitive-local-only")
        self.assertEqual(packet["commit_policy"], "do-not-commit")
        self.assertFalse(packet["contains_human_decisions"])
        self.assertFalse(packet["contains_official_source_snapshots"])
        self.assertFalse(packet["network_fetch_performed"])
        self.assertFalse(packet["production_release_eligible"])
        self.assertEqual(packet["source_count"], 5)
        self.assertEqual(
            packet["registry"],
            {
                "artifact_scope": "fixture-governance-evidence",
                "knowledge_release_id": "kr-2026-07-06.1",
                "path": "data/source_registry/sr-2026-07-06.1.json",
                "sha256": (
                    "b0d775c43f1dc219f227ff416c56df3f4765f48d8b82f83592d8095244af9543"
                ),
                "source_registry_version": "sr-2026-07-06.1",
            },
        )

        by_id = {source["source_id"]: source for source in packet["sources"]}
        source = by_id["nyidanmark-permanent-residence-language-requirements"]
        self.assertEqual(
            source["registry_record"],
            {
                "source_id": "nyidanmark-permanent-residence-language-requirements",
                "official_url": (
                    "https://www.nyidanmark.dk/da/Du-vil-ansoege/Permanent-ophold"
                ),
                "publisher": "SIRI",
                "topic": "permanent-residence language requirements",
                "language": "da",
                "registry_state": "discovered",
            },
        )
        self.assertEqual(
            source["blank_curator_admission"],
            {
                "decision": None,
                "curator_ids": [],
                "admitted_at_utc": None,
                "scope_rationale": None,
                "monitoring_owner_ids": [],
            },
        )

        self.assertEqual(len(packet["machine_observed_discrepancies"]), 1)
        observation = packet["machine_observed_discrepancies"][0]
        self.assertEqual(observation["source_id"], source["source_id"])
        self.assertEqual(observation["classification"], "unverified-machine-observation")
        self.assertTrue(observation["requires_curator_verification"])
        self.assertFalse(observation["counts_as_curation_evidence"])

    def test_packet_refuses_output_inside_repository(self) -> None:
        output_path = ROOT / ".source-admission-packet-test.json"

        with self.assertRaisesRegex(
            SourceAdmissionPacketError,
            "outside the repository",
        ):
            write_source_admission_packet(
                repo_root=ROOT,
                registry_path=REGISTRY_PATH,
                output_path=output_path,
                generated_at_utc="2026-07-14T18:00:00Z",
            )

        self.assertFalse(output_path.exists())

    def test_packet_refuses_registry_after_any_source_leaves_discovery(self) -> None:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        registry["sources"][0]["registry_state"] = "candidate-approved-url"

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            repo_root = workspace / "repo"
            repo_root.mkdir()
            registry_path = repo_root / "registry.json"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            output_path = workspace / "source-admission-packet.json"

            with self.assertRaisesRegex(
                SourceAdmissionPacketError,
                "all sources must be discovered",
            ):
                write_source_admission_packet(
                    repo_root=repo_root,
                    registry_path=registry_path,
                    output_path=output_path,
                    generated_at_utc="2026-07-14T18:00:00Z",
                )

            self.assertFalse(output_path.exists())

    def test_packet_refuses_registry_drift_from_active_fixture_release(self) -> None:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        registry["sources"][0]["publisher"] = "Different publisher"

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            repo_root = workspace / "repo"
            registry_path = (
                repo_root / "data" / "source_registry" / "sr-2026-07-06.1.json"
            )
            registry_path.parent.mkdir(parents=True)
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            release_dir = (
                repo_root / "data" / "knowledge_releases" / "kr-2026-07-06.1"
            )
            shutil.copytree(RELEASE_DIR, release_dir)
            output_path = workspace / "source-admission-packet.json"

            with self.assertRaisesRegex(
                SourceAdmissionPacketError,
                "does not match its active knowledge release",
            ):
                write_source_admission_packet(
                    repo_root=repo_root,
                    registry_path=registry_path,
                    output_path=output_path,
                    generated_at_utc="2026-07-14T18:00:00Z",
                )

            self.assertFalse(output_path.exists())

    def test_packet_reports_malformed_registry_through_its_public_error(self) -> None:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        registry["sources"][0].pop("topic")

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            repo_root = workspace / "repo"
            repo_root.mkdir()
            registry_path = repo_root / "registry.json"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")

            with self.assertRaisesRegex(
                SourceAdmissionPacketError,
                "source registry is invalid",
            ):
                write_source_admission_packet(
                    repo_root=repo_root,
                    registry_path=registry_path,
                    output_path=workspace / "source-admission-packet.json",
                    generated_at_utc="2026-07-14T18:00:00Z",
                )

    def test_cli_writes_packet_with_explicitly_empty_optional_observations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "source-admission-packet.json"

            status = main(
                [
                    "--repo-root",
                    str(ROOT),
                    "--registry",
                    str(REGISTRY_PATH),
                    "--output",
                    str(output_path),
                    "--generated-at-utc",
                    "2026-07-14T18:00:00Z",
                ]
            )

            self.assertEqual(status, 0)
            packet = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(packet["machine_observed_discrepancies"], [])
            self.assertFalse(packet["production_release_eligible"])


if __name__ == "__main__":
    unittest.main()
