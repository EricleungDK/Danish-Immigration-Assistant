import json
import tempfile
import unittest
from pathlib import Path

from danish_rag.release_evaluation import generate_release_evaluation


ROOT = Path(__file__).resolve().parents[1]


def _gate(report, gate_id):
    return next(gate for gate in report["gate_results"] if gate["id"] == gate_id)


class ReleaseEvaluationReportTests(unittest.TestCase):
    def test_report_covers_current_release_gates_and_keeps_release_blocked(self):
        report = generate_release_evaluation(
            ROOT,
            generated_at_utc="2026-07-07T00:00:00Z",
        )
        qualification = json.loads(
            (ROOT / "config" / "release-qualification.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(report["schema_version"], "1.0")
        self.assertEqual(report["generated_at_utc"], "2026-07-07T00:00:00Z")
        self.assertEqual(
            report["release_qualification_id"],
            qualification["qualification_id"],
        )
        self.assertEqual(report["qualification_status"], "blocked")
        self.assertEqual(report["release_decision"], "do-not-release")
        self.assertFalse(report["strict_release_passed"])

        configured_gate_ids = {gate["id"] for gate in qualification["gate_results"]}
        reported_gate_ids = {gate["id"] for gate in report["gate_results"]}
        self.assertEqual(reported_gate_ids, configured_gate_ids)

        blocker_ids = {blocker["id"] for blocker in report["derived_release_blockers"]}
        self.assertIn("quality-bar-human-approval-pending", blocker_ids)
        self.assertIn("environment-matrix-critical-journeys-not-complete", blocker_ids)
        self.assertNotIn("retrieval-required-evidence-baseline", blocker_ids)

    def test_retrieval_gate_uses_hybrid_evidence_and_quality_bar_thresholds(self):
        report = generate_release_evaluation(
            ROOT,
            generated_at_utc="2026-07-07T00:00:00Z",
        )

        gate = _gate(report, "retrieval-required-evidence-baseline")

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["evaluated_status"], "passed")
        self.assertEqual(gate["observed"]["required_evidence_recall_at_3"], 1.0)
        self.assertEqual(gate["observed"]["required_evidence_query_count"], 7)
        self.assertEqual(gate["observed"]["blocked_source_violations"], 0)
        self.assertEqual(gate["observed"]["forbidden_result_violations"], 0)
        self.assertEqual(gate["thresholds"]["required_evidence_recall_at_3_min"], 0.95)
        self.assertEqual(gate["thresholds"]["blocked_source_violations_max"], 0)
        self.assertEqual(gate["thresholds"]["forbidden_result_violations_max"], 0)
        self.assertEqual(gate["failures"], [])

    def test_report_records_privacy_assertions_without_user_content_fields(self):
        report = generate_release_evaluation(
            ROOT,
            generated_at_utc="2026-07-07T00:00:00Z",
        )

        self.assertEqual(
            report["privacy_assertions"],
            {
                "uses_production_user_questions": False,
                "uses_production_answers": False,
                "uses_conversation_identifiers": False,
                "ran_live_network_or_provider_calls": False,
            },
        )

        serialized = json.dumps(report, sort_keys=True).casefold()
        forbidden_fragments = [
            '"production_question_text"',
            '"production_answer_text"',
            '"user_question_text"',
            '"user_answer_text"',
            '"conversation_id"',
            '"conversation_record"',
        ]
        for fragment in forbidden_fragments:
            self.assertNotIn(fragment, serialized)

    def test_default_output_path_is_not_written_by_core_generator(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "release-evaluation-current.json"

            report = generate_release_evaluation(
                ROOT,
                generated_at_utc="2026-07-07T00:00:00Z",
            )

            self.assertFalse(output_path.exists())
            json.dumps(report, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
