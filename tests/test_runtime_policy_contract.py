import json
import unittest
from pathlib import Path

from danish_rag.runtime_policy import (
    extract_documented_policy_contract,
    load_runtime_policy,
    validate_policy_document_contract,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "runtime-policy.json"
DOC_PATH = ROOT / "docs" / "runtime-baseline.md"


class RuntimePolicyContractTests(unittest.TestCase):
    def test_policy_records_the_issue_26_runtime_baseline(self):
        policy = load_runtime_policy(POLICY_PATH)

        self.assertEqual(policy["baseline_id"], "mvp-runtime-baseline-issue-26")
        self.assertEqual(policy["providers"]["initial"]["id"], "ollama")
        self.assertEqual(policy["providers"]["initial"]["minimum_version"], "0.30.6")
        self.assertEqual(policy["models"]["generation"]["initial"], "gemma4:12b")
        self.assertEqual(policy["models"]["embedding"]["provisional_candidate"], "embeddinggemma")
        self.assertFalse(policy["models"]["embedding"]["supported_for_production"])
        self.assertEqual(policy["capabilities"], ["generation", "embedding"])
        self.assertEqual(policy["application"]["process_model"], "single-local-python-process")
        self.assertEqual(policy["application"]["code_updates"], "manual")
        self.assertEqual(policy["knowledge_releases"]["updates"], "explicit-user-approved")

    def test_policy_rejects_non_loopback_defaults(self):
        policy = load_runtime_policy(POLICY_PATH)

        self.assertEqual(policy["application"]["default_bind_host"], "127.0.0.1")
        self.assertEqual(policy["providers"]["initial"]["default_endpoint"], "http://127.0.0.1:11434")
        self.assertTrue(policy["browser_security"]["reject_non_loopback_by_default"])
        self.assertTrue(policy["browser_security"]["validate_host_and_origin_for_state_changes"])

    def test_runtime_documentation_embeds_matching_machine_policy_contract(self):
        policy = load_runtime_policy(POLICY_PATH)
        documented_contract = extract_documented_policy_contract(DOC_PATH)

        self.assertEqual(documented_contract, policy["documentation_contract"])
        self.assertEqual(validate_policy_document_contract(policy, documented_contract), [])

    def test_runtime_documentation_distinguishes_answer_path_from_release_network(self):
        text = DOC_PATH.read_text(encoding="utf-8")
        policy = load_runtime_policy(POLICY_PATH)

        self.assertIn("Local-only answer path", text)
        self.assertIn("Permitted release-network operations", text)
        self.assertFalse(policy["network"]["answer_path_allows_outbound_requests"])
        self.assertTrue(policy["network"]["knowledge_release_checks_allowed"])
        self.assertFalse(policy["privacy"]["put_user_content_in_urls_or_logs"])

    def test_documented_contract_is_valid_json(self):
        documented_contract = extract_documented_policy_contract(DOC_PATH)

        json.dumps(documented_contract)


if __name__ == "__main__":
    unittest.main()
