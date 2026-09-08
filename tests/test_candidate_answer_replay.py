"""Candidate replay must verify signed content without promoting release policy."""

import json
from copy import deepcopy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from danish_rag.evidence_integrity import sha256_file
from danish_rag.final_answer_evaluation import (
    FinalAnswerEvaluationError,
    build_captured_live_ollama_runner,
    generate_final_answer_evaluation,
    main,
)
from danish_rag.knowledge_release import verify_knowledge_release
from danish_rag.retrieval import _attach_source_metadata
from tests.chunk_release_fixture import (
    build_chunked_release_fixture,
    bundled_reviewed_source_and_document,
)
from tests.release_trust_fixture import create_test_release_trust_fixture
from tests.test_final_answer_evaluation import ROOT, _SyntheticApprovedLiveRunner


class CandidateReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.trust = create_test_release_trust_fixture(self.directory / "trust")
        source, document = bundled_reviewed_source_and_document(
            "The reviewed official source describes Prøve i Dansk 2."
        )
        self.release = self.directory / "candidate"
        build_chunked_release_fixture(
            release_dir=self.release,
            release_id="kr-2026-09-04.1",
            source=source,
            document=document,
            release_trust=self.trust,
            created_at_utc="2026-09-05T12:00:00Z",
        )
        self.verified = verify_knowledge_release(
            self.release, trust_root_path=self.trust.trust_root_path
        )
        self.packet = self.directory / "capture.json"
        self.report = self.directory / "report.json"

    def capture(self, *, mutate_evidence=None, mutate_answer=None, failed=False):
        verified = self.verified

        class Runner(_SyntheticApprovedLiveRunner):
            public_identity = {
                **_SyntheticApprovedLiveRunner.public_identity,
                "corpus_id": verified["manifest"]["corpus_id"],
            }

            def run(self, case):
                execution = super().run(case)
                document = verified["documents"][0]
                evidence = _attach_source_metadata(document, verified["manifest"]["sources"][0])
                evidence.update(
                    citation_id=document["document_id"],
                    corpus_identity=self.public_identity["corpus_id"],
                    knowledge_release_id=verified["manifest"]["knowledge_release_id"],
                    retrieval_score=0.1,
                )
                if mutate_evidence:
                    mutate_evidence(evidence)
                if mutate_answer:
                    mutate_answer(execution.result.answer)
                return replace(
                    execution,
                    result=None if failed else replace(
                        execution.result, corpus_identity=self.public_identity["corpus_id"]
                    ),
                    evidence=[evidence],
                    error_type="AnswerValidationError" if failed else "",
                )

        report = generate_final_answer_evaluation(
            ROOT,
            runner=Runner(),
            mode="live-ollama",
            human_review_packet_path=self.packet,
            generated_at_utc="2026-09-05T12:00:00Z",
        )
        self.report.write_text(json.dumps(report))

    def replay(self, **overrides):
        args = {
            "repo_root": ROOT,
            "execution_capture_path": self.packet,
            "execution_capture_sha256": sha256_file(self.packet),
            "capture_report_path": self.report,
            "capture_report_sha256": sha256_file(self.report),
            "candidate_release_dir": self.release,
            "candidate_manifest_sha256": sha256_file(self.release / "manifest.json"),
            "trust_root_path": self.trust.trust_root_path,
        }
        return build_captured_live_ollama_runner(**{**args, **overrides})

    def test_explicit_candidate_replay_verifies_identity_without_promoting_policy(self):
        self.capture()
        policy_before = (ROOT / "config/release-qualification.json").read_bytes()
        runner = self.replay()
        self.assertEqual(runner.public_identity["corpus_id"], "kr-2026-09-04.1")
        self.assertEqual(runner.capture_provenance["qualification_scope"], "explicit-candidate-only")
        self.assertFalse(runner.capture_provenance["release_policy_changed"])
        self.assertEqual(policy_before, (ROOT / "config/release-qualification.json").read_bytes())
        with self.assertRaisesRegex(FinalAnswerEvaluationError, "approved runtime"):
            self.replay(candidate_release_dir=None, candidate_manifest_sha256=None, trust_root_path=None)

    def test_hash_bound_capture_cannot_substitute_unsigned_evidence(self):
        for field, value in (
            ("content", "Unsigned altered content"),
            ("official_url", "https://example.org"),
            ("reviewers", ["Unsigned reviewer"]),
            ("extra_unbound_trust", True),
        ):
            with self.subTest(field=field):
                self.capture(mutate_evidence=lambda evidence: evidence.update({field: value}))
                with self.assertRaisesRegex(FinalAnswerEvaluationError, "differs from signed candidate"):
                    self.replay()

    def test_candidate_replay_retains_error_free_and_exact_manifest_gates(self):
        self.capture(failed=True)
        with self.assertRaisesRegex(FinalAnswerEvaluationError, "error-free"):
            self.replay()
        self.capture()
        with self.assertRaisesRegex(FinalAnswerEvaluationError, "expected SHA-256"):
            self.replay(candidate_manifest_sha256="0" * 64)
        with self.assertRaisesRegex(FinalAnswerEvaluationError, "trust root"):
            self.replay(trust_root_path=None)
        documents = self.release / "corpus" / "documents.json"
        documents.write_text("[]")
        with self.assertRaisesRegex(Exception, "hash does not match"):
            self.replay()

    def test_cli_explicit_candidate_options_replay_without_provider(self):
        self.capture()
        output = self.directory / "replay.json"
        arguments = [
            "--repo-root", str(ROOT), "--mode", "captured-live-ollama",
            "--execution-capture", str(self.packet),
            "--execution-capture-sha256", sha256_file(self.packet),
            "--capture-report", str(self.report),
            "--capture-report-sha256", sha256_file(self.report),
            "--candidate-release-dir", str(self.release),
            "--candidate-manifest-sha256", sha256_file(self.release / "manifest.json"),
            "--trust-root-path", str(self.trust.trust_root_path),
            "--output", str(output),
        ]
        self.assertEqual(main(arguments), 0)
        report = json.loads(output.read_text())
        self.assertFalse(report["execution"]["live_provider_calls"])
        self.assertFalse(report["strict_passed"])
        self.assertEqual(report["capture_provenance"]["qualification_scope"], "explicit-candidate-only")

    def test_partial_metadata_survives_replay_without_human_pass_and_cannot_be_changed(self):
        metadata = {
            "status": "partial",
            "retained_official_fact_count": 1,
            "omitted_section_count": 2,
            "omitted_reasons": {
                "unsupported": 1, "hard_constraint": 1,
                "dependent_statement": 0, "non_fact": 0,
            },
        }
        document = self.verified["documents"][0]

        def partial_answer(answer):
            answer["verification"] = deepcopy(metadata)
            answer["sections"] = [
                {"kind": "official_fact", "text": document["content"],
                 "citation_ids": [document["document_id"]]},
                {"kind": "refusal", "text": (
                    "This answer is partial. Some generated statements did not pass "
                    "the complete verification process and have been omitted."
                ), "citation_ids": []},
            ]

        self.capture(mutate_answer=partial_answer)
        packet = json.loads(self.packet.read_text())
        runner = self.replay()
        for record in packet["cases"]:
            self.assertEqual(record["execution"]["result"]["answer"]["verification"], metadata)
            restored = runner.run({"id": record["case_id"]})
            self.assertEqual(restored.result.answer["verification"], metadata)
            self.assertIn("This answer is partial.", restored.result.answer["sections"][-1]["text"])

        replay_report = generate_final_answer_evaluation(
            ROOT, runner=runner, mode="captured-live-ollama",
        )
        capture_report = json.loads(self.report.read_text())
        self.assertFalse(replay_report["strict_passed"])
        self.assertFalse(replay_report["adjudications"]["provided"])
        self.assertEqual(replay_report["adjudications"]["independent_human_case_count"], 0)
        for metric in ("required_fact_coverage", "forbidden_claims", "privacy_requirement_compliance"):
            self.assertEqual(replay_report["metrics"][metric]["status"], "not_evaluable")
            self.assertEqual(replay_report["metrics"][metric], capture_report["metrics"][metric])

        # Even supplying the new whole-file hash cannot conceal changing the
        # partial status/counts: the internal execution binding covers them too.
        packet["cases"][0]["execution"]["result"]["answer"]["verification"]["omitted_section_count"] = 0
        self.packet.write_text(json.dumps(packet))
        with self.assertRaisesRegex(FinalAnswerEvaluationError, "execution SHA-256 does not match"):
            self.replay()
