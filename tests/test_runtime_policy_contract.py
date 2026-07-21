from copy import deepcopy
import json
import unittest
from pathlib import Path

from danish_rag.runtime_policy import (
    extract_documented_policy_contract,
    load_runtime_policy,
    validate_policy_document_contract,
    validate_runtime_baseline_prose_contract,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "runtime-policy.json"
DOC_PATH = ROOT / "docs" / "runtime-baseline.md"


class RuntimePolicyContractTests(unittest.TestCase):
    def assertProseContradictions(
        self,
        policy,
        contradictions,
        expected_failure_fragment,
    ):
        for label, contradictory_text in contradictions.items():
            with self.subTest(label=label):
                failures = validate_runtime_baseline_prose_contract(
                    policy, contradictory_text
                )
                self.assertTrue(
                    any(expected_failure_fragment in failure for failure in failures),
                    f"{label} failures did not include "
                    f"{expected_failure_fragment!r}: {failures}",
                )

    def test_policy_records_local_only_answer_path_runtime_baseline(self):
        policy = load_runtime_policy(POLICY_PATH)

        self.assertEqual(policy["baseline_id"], "mvp-runtime-baseline-issue-26")
        self.assertEqual(policy["providers"]["initial"]["id"], "ollama")
        self.assertEqual(policy["providers"]["initial"]["minimum_version"], "0.30.6")
        self.assertEqual(policy["models"]["generation"]["initial"], "gemma4:12b")
        self.assertEqual(policy["models"]["embedding"]["initial_supported"], "embeddinggemma")
        self.assertTrue(policy["models"]["embedding"]["supported_for_production"])
        self.assertEqual(
            policy["models"]["embedding"]["approval_status"],
            "approved-by-issue-4",
        )
        self.assertEqual(policy["capabilities"], ["generation", "embedding"])
        self.assertEqual(policy["application"]["process_model"], "single-local-python-process")
        self.assertEqual(policy["application"]["code_updates"], "manual")
        self.assertEqual(policy["knowledge_releases"]["updates"], "explicit-user-approved")
        self.assertEqual(
            policy["supported_environment"]["browser_release_baseline"][
                "minimum_chromium_major"
            ],
            150,
        )
        self.assertEqual(
            policy["supported_environment"]["browser_release_baseline"][
                "maintenance"
            ],
            "review-before-each-release-qualification",
        )

    def test_policy_rejects_non_loopback_defaults(self):
        policy = load_runtime_policy(POLICY_PATH)

        self.assertEqual(policy["application"]["default_bind_host"], "127.0.0.1")
        self.assertEqual(
            policy["providers"]["initial"]["default_endpoint"],
            "http://127.0.0.1:11434",
        )
        self.assertTrue(policy["browser_security"]["reject_non_loopback_by_default"])
        self.assertTrue(policy["browser_security"]["validate_host_and_origin_for_state_changes"])

    def test_runtime_documentation_embeds_matching_machine_policy_contract(self):
        policy = load_runtime_policy(POLICY_PATH)
        documented_contract = extract_documented_policy_contract(DOC_PATH)

        self.assertEqual(documented_contract, policy["documentation_contract"])
        self.assertEqual(validate_policy_document_contract(policy, documented_contract), [])

    def test_local_only_answer_path_documentation_matches_runtime_policy_prose_contract(self):
        text = DOC_PATH.read_text(encoding="utf-8")
        policy = load_runtime_policy(POLICY_PATH)

        self.assertIn("Local-only answer path", text)
        self.assertIn("Permitted release-network operations", text)
        self.assertFalse(policy["network"]["answer_path_allows_outbound_requests"])
        self.assertTrue(policy["network"]["knowledge_release_checks_allowed"])
        self.assertFalse(policy["privacy"]["put_user_content_in_urls_or_logs"])
        self.assertEqual(validate_runtime_baseline_prose_contract(policy, text), [])

    def test_local_only_answer_path_prose_rejects_provider_mandates(self):
        text = DOC_PATH.read_text(encoding="utf-8")
        policy = load_runtime_policy(POLICY_PATH)
        contradictory_text = text.replace(
            "This is an initial adapter decision, not a permanent product mandate.",
            (
                "This is a permanent product mandate and Ollama is mandatory "
                "for all future providers."
            ),
        )

        failures = validate_runtime_baseline_prose_contract(policy, contradictory_text)

        self.assertTrue(any("provider neutrality" in failure for failure in failures))

    def test_local_only_answer_path_prose_rejects_generation_and_embedding_boundaries(self):
        text = DOC_PATH.read_text(encoding="utf-8")
        policy = load_runtime_policy(POLICY_PATH)
        contradictions = {
            "generation model as official source": text.replace(
                (
                    "It is not an approved official source and cannot supply "
                    "official facts from model knowledge."
                ),
                (
                    "It is an approved official source and may supply official "
                    "facts from model knowledge."
                ),
            ),
            "generation and embedding merged": text.replace(
                "Generation and embedding are separate capabilities:",
                "Generation and embedding are interchangeable capabilities:",
            ),
            "embedding approval silently reversed": text.replace(
                (
                    "`embeddinggemma` is the initial supported embedding model. "
                    "Issue #4 approved it after the issue #29 retrieval benchmark."
                ),
                (
                    "`embeddinggemma` is only a provisional embedding candidate "
                    "and is not supported after issue #4 approval."
                ),
            ),
        }

        self.assertProseContradictions(
            policy,
            contradictions,
            "generation and embedding",
        )

    def test_generation_and_embedding_prose_tracks_policy_capability_contracts(self):
        text = DOC_PATH.read_text(encoding="utf-8")
        policy = load_runtime_policy(POLICY_PATH)
        drifts = {
            "generation input": (
                ("generation", "input"),
                ["messages", "response_schema", "tool_options"],
            ),
            "generation output": (
                ("generation", "output"),
                [
                    "structured_answer",
                    "provider_identity",
                    "model_identity",
                    "trace_id",
                ],
            ),
            "embedding input": (("embedding", "input"), ["document_chunks"]),
            "embedding output": (
                ("embedding", "output"),
                ["vectors", "model_identity", "vector_dimensions", "token_counts"],
            ),
        }

        for label, ((capability, boundary), drifted_fields) in drifts.items():
            with self.subTest(label=label):
                drifted_policy = deepcopy(policy)
                drifted_policy["capability_contracts"][capability][boundary] = (
                    drifted_fields
                )

                failures = validate_runtime_baseline_prose_contract(
                    drifted_policy, text
                )

                self.assertTrue(
                    any("generation and embedding" in failure for failure in failures),
                    f"{label} drift was not reported: {failures}",
                )

    def test_local_only_answer_path_prose_rejects_loopback_security_contradictions(self):
        text = DOC_PATH.read_text(encoding="utf-8")
        policy = load_runtime_policy(POLICY_PATH)
        contradictions = {
            "non-loopback default": text.replace(
                "Non-loopback application exposure is unsupported in the MVP baseline.",
                "Non-loopback application exposure is supported by default in the MVP baseline.",
            ),
            "missing host origin validation": text.replace(
                (
                    "State-changing browser requests must validate Host and "
                    "Origin once the web application exists."
                ),
                (
                    "State-changing browser requests do not need Host or "
                    "Origin validation once the web application exists."
                ),
            ),
        }

        self.assertProseContradictions(policy, contradictions, "loopback")

    def test_knowledge_release_prose_rejects_update_separation_contradictions(self):
        text = DOC_PATH.read_text(encoding="utf-8")
        policy = load_runtime_policy(POLICY_PATH)
        contradictions = {
            "knowledge release merged with code": text.replace(
                "Knowledge release installation is separate from application-code updates.",
                "Knowledge release installation is bundled with application-code updates.",
            ),
            "git pull allowed": text.replace(
                "The running product must not use `git pull` as an update mechanism.",
                "The running product should use `git pull` as an update mechanism.",
            ),
        }

        self.assertProseContradictions(policy, contradictions, "Knowledge release")

    def test_local_only_answer_path_prose_rejects_verified_environment_contradictions(self):
        text = DOC_PATH.read_text(encoding="utf-8")
        policy = load_runtime_policy(POLICY_PATH)
        contradictions = {
            "macos verified": text.replace(
                (
                    "macOS and native Linux remain candidates for the final "
                    "supported-environment matrix, but issue #26 does not claim "
                    "them verified."
                ),
                "macOS and native Linux are verified environments for issue #26.",
            ),
            "cpu latency guaranteed": text.replace(
                "CPU-only compatibility and latency are measured rather than guaranteed.",
                "CPU-only compatibility and latency are guaranteed.",
            ),
        }

        self.assertProseContradictions(policy, contradictions, "environment")

    def test_documented_contract_is_valid_json(self):
        documented_contract = extract_documented_policy_contract(DOC_PATH)

        json.dumps(documented_contract)


if __name__ == "__main__":
    unittest.main()
