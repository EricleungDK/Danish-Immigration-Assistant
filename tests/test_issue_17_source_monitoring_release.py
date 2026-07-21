import json
import shutil
import tempfile
import unittest
from pathlib import Path

from danish_rag.knowledge_release import (
    BUNDLED_MINIMAL_RELEASE,
    KnowledgeReleaseError,
    verify_knowledge_release,
)
from danish_rag.release_trust import sign_manifest
from danish_rag.source_maintenance import (
    approve_source_check,
    build_publishable_knowledge_release,
    capture_source_check,
)
from tests.release_trust_fixture import create_test_release_trust_fixture


class Issue17SourceMonitoringReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.release_trust = create_test_release_trust_fixture(
            self.root / "test-only-release-trust"
        )
        self.registry_source = {
            "source_id": "nyidanmark-fixture-source",
            "publisher": "SIRI",
            "title": "Fixture official source",
            "official_url": "https://example.test/source",
            "final_url": "https://example.test/source",
            "topic": "permanent-residence language requirements",
            "language": "da",
            "review_state": "approved-current",
            "reviewers": ["previous-reviewer"],
            "reviewed_at_utc": "2026-07-01T12:00:00Z",
            "last_checked_at_utc": "2026-07-01T12:00:00Z",
            "source_content_sha256": (
                "34a780ad578b997db55b260beb60b501f3e04d30ba1a51fcf43cd8dd1241780d"
            ),
            "normalized_document_sha256": (
                "34a780ad578b997db55b260beb60b501f3e04d30ba1a51fcf43cd8dd1241780d"
            ),
            "extraction_schema_version": "1.0",
            "fresh_tomato_inputs": {
                "next_review_due_utc": "2026-10-01T12:00:00Z",
                "source_health": "current",
            },
        }

    def test_checks_capture_metadata_and_hold_changed_content_for_human_review(self):
        check = capture_source_check(
            self.registry_source,
            {
                "status_code": 200,
                "final_url": "https://example.test/source",
                "headers": {
                    "etag": '"abc123"',
                    "last-modified": "Tue, 07 Jul 2026 10:00:00 GMT",
                },
                "body": "Official page changed. Application deadline is 31 August 2026.",
            },
            extracted_text="Official page changed. Application deadline is 31 August 2026.",
            checked_at_utc="2026-07-07T10:00:00Z",
            visible_dates=[],
        )

        self.assertEqual(check["review_state"], "changed-unreviewed")
        self.assertEqual(check["source_health"], "changed-unreviewed")
        self.assertEqual(check["http"]["status_code"], 200)
        self.assertEqual(check["http"]["final_url"], "https://example.test/source")
        self.assertEqual(check["http"]["metadata"]["etag"], '"abc123"')
        self.assertEqual(check["extraction"]["outcome"], "succeeded")
        self.assertTrue(check["hashes"]["source_content_sha256"])
        self.assertTrue(check["hashes"]["normalized_document_sha256"])
        self.assertEqual(check["visible_dates"], [])
        self.assertIn("human review", check["policy"]["release_gate"])

    def test_redirect_failed_extraction_broken_overdue_and_unapproved_follow_policy(self):
        cases = [
            (
                {"status_code": 200, "final_url": "https://example.test/moved", "body": "same"},
                "same",
                self.registry_source,
                "redirected-pending-review",
                False,
            ),
            (
                {"status_code": 200, "final_url": "https://example.test/source", "body": "same"},
                None,
                self.registry_source,
                "extraction-failed",
                False,
            ),
            (
                {"status_code": 404, "final_url": "https://example.test/source", "body": "not found"},
                "not found",
                self.registry_source,
                "broken",
                False,
            ),
            (
                {"status_code": 200, "final_url": "https://example.test/source", "body": "old content"},
                "old content",
                {
                    **self.registry_source,
                    "fresh_tomato_inputs": {
                        "next_review_due_utc": "2026-06-01T00:00:00Z",
                        "source_health": "current",
                    },
                    "source_content_sha256": (
                        "34a780ad578b997db55b260beb60b501f3e04d30ba1a51fcf43cd8dd1241780d"
                    ),
                    "normalized_document_sha256": (
                        "34a780ad578b997db55b260beb60b501f3e04d30ba1a51fcf43cd8dd1241780d"
                    ),
                },
                "overdue-policy-usable",
                True,
            ),
            (
                {"status_code": 200, "final_url": "https://example.test/source", "body": "old content"},
                "old content",
                {
                    **self.registry_source,
                    "fresh_tomato_inputs": {
                        "next_review_due_utc": "2026-06-01T00:00:00Z",
                        "overdue_blocked_after_utc": "2026-07-01T00:00:00Z",
                        "source_health": "current",
                    },
                    "source_content_sha256": (
                        "34a780ad578b997db55b260beb60b501f3e04d30ba1a51fcf43cd8dd1241780d"
                    ),
                    "normalized_document_sha256": (
                        "34a780ad578b997db55b260beb60b501f3e04d30ba1a51fcf43cd8dd1241780d"
                    ),
                },
                "overdue-blocked",
                False,
            ),
            (
                {"status_code": 200, "final_url": "https://example.test/source", "body": "same"},
                "same",
                {**self.registry_source, "review_state": "candidate-approved-url"},
                "changed-unreviewed",
                False,
            ),
        ]

        for fetch_result, extracted_text, source, expected_state, expected_eligible in cases:
            with self.subTest(expected_state=expected_state):
                check = capture_source_check(
                    source,
                    fetch_result,
                    extracted_text=extracted_text,
                    checked_at_utc="2026-07-07T10:00:00Z",
                )
                self.assertEqual(check["review_state"], expected_state)
                self.assertEqual(check["policy"]["release_eligible"], expected_eligible)

    def test_publishable_release_requires_human_approval_for_changed_content(self):
        check = capture_source_check(
            self.registry_source,
            {
                "status_code": 200,
                "final_url": "https://example.test/source",
                "body": "Reviewed official content.",
            },
            extracted_text="Reviewed official content.",
            checked_at_utc="2026-07-07T10:00:00Z",
        )
        document = {
            "document_id": "doc-fixture",
            "source_id": "nyidanmark-fixture-source",
            "title": "Fixture official source",
            "publisher": "SIRI",
            "official_url": "https://example.test/source",
            "final_url": "https://example.test/source",
            "language": "da",
            "topic_tags": ["permanent-residence", "language-requirement"],
            "review_state": "changed-unreviewed",
            "source_health": "changed-unreviewed",
            "approval_state": "unapproved",
            "checked_at_utc": "2026-07-07T10:00:00Z",
            "content": "Reviewed official content.",
        }

        with self.assertRaisesRegex(KnowledgeReleaseError, "not release-eligible"):
            build_publishable_knowledge_release(
                release_dir=self.root / "blocked-release",
                release_id="kr-2026-07-07.1",
                source_registry_version="sr-2026-07-07.1",
                sources=[check],
                documents=[document],
                created_at_utc="2026-07-07T12:00:00Z",
                minimum_application_version="0.1.0",
                signing_private_key_path=(
                    self.release_trust.signing_private_key_path
                ),
                trust_root_path=self.release_trust.trust_root_path,
            )

        approved_source = approve_source_check(
            check,
            reviewer_id="human-reviewer",
            reviewed_at_utc="2026-07-07T11:00:00Z",
            next_review_due_utc="2026-10-07T11:00:00Z",
        )
        approved_document = {
            **document,
            "review_state": "approved-current",
            "source_health": "healthy",
            "approval_state": "approved",
        }
        release = build_publishable_knowledge_release(
            release_dir=self.root / "approved-release",
            release_id="kr-2026-07-07.1",
            source_registry_version="sr-2026-07-07.1",
            sources=[approved_source],
            documents=[approved_document],
            created_at_utc="2026-07-07T12:00:00Z",
            minimum_application_version="0.1.0",
            signing_private_key_path=self.release_trust.signing_private_key_path,
            trust_root_path=self.release_trust.trust_root_path,
        )

        manifest = release["manifest"]
        self.assertEqual(manifest["sources"][0]["review_state"], "approved-current")
        self.assertEqual(manifest["sources"][0]["reviewers"], ["human-reviewer"])
        self.assertEqual(manifest["minimum_application_version"], "0.1.0")
        self.assertEqual(manifest["manifest_schema_version"], "1.0")
        self.assertEqual(manifest["corpus_schema_version"], "1.0")
        self.assertEqual(
            manifest["artifacts"][0]["sha256"],
            release["artifact"]["sha256"],
        )
        verify_knowledge_release(
            self.root / "approved-release",
            trust_root_path=self.release_trust.trust_root_path,
        )

    def test_release_verification_accepts_fixture_and_rejects_contract_gaps(self):
        verified = verify_knowledge_release(BUNDLED_MINIMAL_RELEASE)
        integrity = verified["manifest"]["integrity"]
        self.assertEqual(integrity["signature_algorithm"], "ed25519")
        self.assertEqual(integrity["signature"], "manifest.sig")
        signature_path = BUNDLED_MINIMAL_RELEASE / integrity["signature"]
        self.assertEqual(len(signature_path.read_bytes()), 64)

        for mutator, error in [
            (
                lambda manifest: manifest["sources"][0].pop("reviewers"),
                "reviewer",
            ),
            (
                lambda manifest: manifest["sources"][0].pop("official_url"),
                "provenance",
            ),
            (
                lambda manifest: manifest.__setitem__("minimum_application_version", "99.0.0"),
                "requires application",
            ),
            (
                lambda manifest: manifest["integrity"].pop("signature"),
                "integrity",
            ),
        ]:
            with self.subTest(error=error):
                release_dir = self.root / f"bad-{error.replace(' ', '-')}"
                shutil.copytree(BUNDLED_MINIMAL_RELEASE, release_dir)
                manifest_path = release_dir / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mutator(manifest)
                manifest["integrity"]["trust_root_id"] = (
                    self.release_trust.trust_root_id
                )
                manifest_path.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                sign_manifest(
                    manifest_path,
                    self.release_trust.signing_private_key_path,
                    release_dir / "manifest.sig",
                )

                with self.assertRaisesRegex(KnowledgeReleaseError, error):
                    verify_knowledge_release(
                        release_dir,
                        trust_root_path=self.release_trust.trust_root_path,
                    )

    def test_release_verification_rejects_manifest_tampering_after_signing(self):
        release_dir = self.root / "tampered-signed-release"
        shutil.copytree(BUNDLED_MINIMAL_RELEASE, release_dir)
        manifest_path = release_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["created_at_utc"] = "2026-07-14T00:00:00Z"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(KnowledgeReleaseError, "signature verification"):
            verify_knowledge_release(release_dir)


if __name__ == "__main__":
    unittest.main()
