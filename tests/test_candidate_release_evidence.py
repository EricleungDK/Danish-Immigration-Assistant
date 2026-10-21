import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from danish_rag.candidate_release_evidence import read_artifact, validate_candidate_evidence
from danish_rag.release_evaluation import generate_release_evaluation
from tests.evidence_rebind import copy_repository, rebind_implementation_hashes

ROOT = Path(__file__).resolve().parents[1]


class CandidateReleaseEvidenceTests(unittest.TestCase):
    # ADR 0001 (docs/adr/0001-requalify-per-release-not-per-commit.md): committed evidence
    # binds implementation hashes and is stale after refactors, so these tests run against
    # a copy whose bindings are recomputed from the current files.
    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._directory.cleanup)
        cls.root = copy_repository(cls._directory.name)
        rebind_implementation_hashes(cls.root)

    def setUp(self):
        self.qualification = json.loads((self.root / "config/release-qualification.json").read_text())

    def test_committed_evidence_is_stale_after_implementation_changes(self):
        # ADR 0001: the committed issue-51 hashes record the qualified candidate, not the
        # current tree, so the release gates reject them until the next requalification.
        report = generate_release_evaluation(ROOT)
        self.assertFalse(report["strict_release_passed"])
        failures = [text for gate in report["gate_results"] for text in gate["failures"]]
        self.assertTrue(any("implementation changed" in text for text in failures), failures)

    def test_current_binding_verifies_with_explicit_release_approval(self):
        self.assertEqual(validate_candidate_evidence(self.root, self.qualification), [])
        report = generate_release_evaluation(self.root)
        self.assertTrue(report["strict_release_passed"])
        self.assertTrue(report["technical_gates_passed"])
        blockers = {item["id"] for item in report["derived_release_blockers"]}
        self.assertNotIn("retrieval-required-evidence-baseline", blockers)
        self.assertNotIn("production-release-owner-approval-pending", blockers)

    def test_missing_owner_approval_still_blocks_release(self):
        qualification = copy.deepcopy(self.qualification)
        next(a for a in qualification['human_approval_records'] if a['id'] == 'production-release-owner-approval-pending')['status'] = 'pending'
        with patch('danish_rag.release_evaluation.load_release_qualification', return_value=qualification):
            report = generate_release_evaluation(self.root)
        self.assertFalse(report['strict_release_passed'])
        self.assertIn('production-release-owner-approval-pending', {b['id'] for b in report['derived_release_blockers']})

    def test_malformed_or_escaping_bindings_produce_structured_failed_report(self):
        for bad in (None, [], {"final_answer": {"path": "../outside", "sha256": "0" * 64}}):
            with self.subTest(binding=bad):
                qualification = copy.deepcopy(self.qualification)
                qualification["candidate_evidence"]["artifacts"] = bad
                with patch("danish_rag.release_evaluation.load_release_qualification", return_value=qualification):
                    report = generate_release_evaluation(self.root)
                self.assertFalse(report["strict_release_passed"])
                self.assertTrue(report["config_validation"]["source_contract"])

    def test_mismatched_installation_and_browser_provenance_are_rejected(self):
        # Exercise semantic validation independently of the preceding file-hash boundary.
        for artifact, field, value in (("production_install", "candidate_identity", None),
                                       ("production_install", "candidate_identity", []),
                                       ("production_install", "knowledge_release_id", "other"),
                                       ("candidate_browser", "mode", "fixture"),
                                       ("candidate_browser", "model", "other-model")):
            with self.subTest(artifact=artifact, field=field):
                def altered(root, candidate, name):
                    result = read_artifact(root, candidate, name)
                    if name == artifact:
                        result[field] = value
                    return result
                with patch("danish_rag.candidate_release_evidence.read_artifact", side_effect=altered):
                    failures = validate_candidate_evidence(self.root, self.qualification)
                self.assertTrue(failures)

    def test_invalid_signature_binding_and_backup_cannot_be_approved(self):
        qualification = copy.deepcopy(self.qualification)
        qualification["candidate_evidence"]["manifest_sha256"] = "0" * 64
        self.assertTrue(validate_candidate_evidence(self.root, qualification))
        def absent_backup(root, candidate, name):
            result = read_artifact(root, candidate, name)
            if name == "signing_custody":
                result["custody"]["backup_status"] = "pending-owner-off-device-backup"
            return result
        with patch("danish_rag.candidate_release_evidence.read_artifact", side_effect=absent_backup):
            self.assertTrue(validate_candidate_evidence(self.root, self.qualification))
