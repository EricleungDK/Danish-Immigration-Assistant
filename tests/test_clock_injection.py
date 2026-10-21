"""#70: freshness, eligibility and evidence checks take an explicit evaluation time."""

import inspect
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from danish_rag import evidence_integrity
from danish_rag.answer_pipeline import AnswerService
from danish_rag.knowledge_release import install_minimal_knowledge_release
from danish_rag.retrieval import HybridRetriever
from danish_rag.trust_indicators import assess_source_freshness
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture
from tests.evaluation_time import fixed_clock
from tests.test_answer_policy_provenance import CLAIM, QUESTION, SourceOnlyVerifier
from tests.test_issue_13_evidence_safety import (
    FixtureRetriever,
    evidence_fixture,
    provider_configuration,
)
from tests.test_source_freshness import source_evidence

DUE = "2026-10-06T12:00:00Z"
BEFORE_DUE = datetime(2026, 10, 1, tzinfo=timezone.utc)
AFTER_DUE = datetime(2026, 10, 7, tzinfo=timezone.utc)


class CoreChecksRequireEvaluationTimeTests(unittest.TestCase):
    def test_freshness_has_no_wall_clock_fallback(self) -> None:
        with self.assertRaises(TypeError):
            assess_source_freshness(source_evidence(due=DUE))
        with self.assertRaises(ValueError):
            assess_source_freshness(source_evidence(due=DUE), evaluated_at_utc=None)

    def test_freshness_rejects_naive_evaluation_time(self) -> None:
        with self.assertRaises(ValueError):
            assess_source_freshness(
                source_evidence(due=DUE), evaluated_at_utc=datetime(2026, 10, 1)
            )
        with self.assertRaises(ValueError):
            assess_source_freshness(
                source_evidence(due=DUE), evaluated_at_utc="2026-10-01T00:00:00"
            )

    def test_freshness_follows_the_given_time(self) -> None:
        evidence = source_evidence(due=DUE)
        self.assertTrue(
            assess_source_freshness(evidence, evaluated_at_utc=BEFORE_DUE).answer_eligible
        )
        self.assertFalse(
            assess_source_freshness(evidence, evaluated_at_utc=AFTER_DUE).answer_eligible
        )

    def test_evidence_integrity_timestamps_come_from_an_explicit_time(self) -> None:
        self.assertFalse(hasattr(evidence_integrity, "utc_now_seconds"))
        self.assertEqual(
            evidence_integrity.format_utc_seconds(
                datetime(2026, 10, 1, 3, 4, 5, 999, tzinfo=timezone.utc)
            ),
            "2026-10-01T03:04:05Z",
        )
        with self.assertRaises(ValueError):
            evidence_integrity.format_utc_seconds(datetime(2026, 10, 1))
        with self.assertRaises(TypeError):
            evidence_integrity.format_utc_seconds()

    def test_system_clock_is_timezone_aware_utc(self) -> None:
        now = evidence_integrity.system_utc_clock()
        self.assertEqual(now.utcoffset(), timedelta(0))


class RetrievalClockTests(unittest.TestCase):
    def test_retrieval_eligibility_uses_the_injected_clock_not_the_wall_clock(self) -> None:
        provider = DeterministicEmbeddingProviderFixture()
        with tempfile.TemporaryDirectory() as data_dir:
            install_minimal_knowledge_release(
                data_dir, embedding_provider=provider, clock=fixed_clock
            )
            now = [BEFORE_DUE]
            retriever = HybridRetriever.from_data_dir(
                data_dir, embedding_provider=provider, clock=lambda: now[0]
            )
            question = "What Danish test is needed for permanent residence?"
            self.assertTrue(retriever.retrieve(question))
            now[0] = AFTER_DUE
            for result in retriever.retrieve(question):
                due = result["fresh_tomato_inputs"]["next_review_due_utc"]
                self.assertGreater(due, "2026-10-07")

    def test_install_indexes_with_the_injected_clock(self) -> None:
        provider = DeterministicEmbeddingProviderFixture()
        with tempfile.TemporaryDirectory() as data_dir:
            install_minimal_knowledge_release(
                data_dir, embedding_provider=provider, clock=lambda: AFTER_DUE
            )
            # Indexed under a later clock; retrieving at the fixed time still works.
            HybridRetriever.from_data_dir(
                data_dir, embedding_provider=provider, clock=fixed_clock
            )

    def test_clock_parameters_exist_on_entry_points(self) -> None:
        for target in (
            HybridRetriever.__init__,
            HybridRetriever.from_data_dir,
            AnswerService.__init__,
            install_minimal_knowledge_release,
        ):
            self.assertIn("clock", inspect.signature(target).parameters, target)


class AnswerServiceClockTests(unittest.TestCase):
    def answer(self, clock):
        source = evidence_fixture("source", content=CLAIM)
        source["fresh_tomato_inputs"] = {
            "source_health": "current",
            "next_review_due_utc": DUE,
            "overdue_blocked_after_utc": "2026-12-01T00:00:00Z",
        }
        return AnswerService(
            retriever=FixtureRetriever([source]),
            generator=SourceOnlyVerifier(),
            clock=clock,
        ).answer(QUESTION, replace(provider_configuration(), provider_id="ollama"))

    def test_fresh_tomato_score_depends_only_on_inputs_and_injected_time(self) -> None:
        before = self.answer(lambda: BEFORE_DUE)
        again = self.answer(lambda: BEFORE_DUE)
        after = self.answer(lambda: AFTER_DUE)
        self.assertEqual(before.answer["trust"]["fresh_tomato_score"], "High")
        self.assertEqual(before.answer, again.answer)
        self.assertEqual(after.answer["trust"]["fresh_tomato_score"], "Medium")

    def test_service_default_clock_is_system_utc(self) -> None:
        service = AnswerService(
            retriever=FixtureRetriever([]), generator=SourceOnlyVerifier()
        )
        self.assertEqual(service.clock().utcoffset(), timedelta(0))


class CollectorCliClockTests(unittest.TestCase):
    """The collector CLIs build their retriever with the clock they evaluate at."""

    def run_main(self, module, argv, collect_name):
        from unittest import mock

        sentinel = lambda: BEFORE_DUE  # noqa: E731
        with mock.patch(
            "danish_rag.retrieval.HybridRetriever.from_data_dir"
        ) as from_data_dir, mock.patch.object(
            module, collect_name, return_value={"strict_passed": True}
        ) as collect, mock.patch("pathlib.Path.write_text"), mock.patch.object(
            module, "baseline_metrics", return_value={}, create=True
        ), mock.patch.object(module, "sha256_file", return_value="0" * 64
        ), mock.patch("json.dumps", return_value="{}"):
            module.main(*argv, clock=sentinel)
        self.assertIs(from_data_dir.call_args.kwargs.get("clock"), sentinel)
        self.assertIs(collect.call_args.kwargs.get("clock"), sentinel)

    def test_grounded_flexibility_retrieval_main(self):
        from danish_rag import grounded_flexibility_retrieval as module

        self.run_main(
            module, (["--trust-root-path", "t", "--output", "o"],), "evaluate_retrieval"
        )

    def test_candidate_retrieval_main(self):
        import sys
        from unittest import mock
        from danish_rag import candidate_retrieval_evidence as module

        with mock.patch.object(sys, "argv", ["x", "--trust-root-path", "t", "--output", "o"]):
            self.run_main(module, (), "collect_baseline")

    def test_approved_retrieval_main(self):
        import sys
        from unittest import mock
        from danish_rag import approved_retrieval_evidence as module

        with mock.patch.object(sys, "argv", ["x", "--output", "o"]), mock.patch.object(
            module, "safe_path"
        ):
            self.run_main(module, (), "collect")
