"""Both approved retrieval scopes must pass independently and fail closed."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from danish_rag.approved_retrieval_evidence import evaluate_approved_retrieval, fixture_metrics
from danish_rag.candidate_release_evidence import read_artifact

ROOT = Path(__file__).resolve().parents[1]


class ApprovedRetrievalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.qualification = json.loads((ROOT/'config/release-qualification.json').read_text())
        self.candidate = self.qualification['candidate_evidence']
        self.quality = json.loads((ROOT/'config/evaluation-quality-bar.json').read_text())
        self.runtime = json.loads((ROOT/'config/runtime-policy.json').read_text())

    def evaluate(self):
        return evaluate_approved_retrieval(ROOT, self.quality, self.runtime, self.candidate)

    def altered(self, name, mutate):
        def read(root, candidate, key):
            result = read_artifact(root, candidate, key)
            if key == name:
                mutate(result)
            return result
        with patch('danish_rag.approved_retrieval_evidence.read_artifact', side_effect=read):
            return self.evaluate()

    def test_approved_scopes_pass_without_rewriting_historical_misses(self):
        result = self.evaluate()
        self.assertEqual(result[5], [])
        self.assertEqual(result[0], 'passed')
        self.assertEqual(result[2]['critical_case_recall_at_3'], 1)
        self.assertEqual(result[2]['critical_case_count'], 15)
        self.assertEqual(result[2]['mandatory_fixture_regressions']['required_evidence_hits'], 7)
        self.assertEqual(result[2]['mandatory_fixture_regressions']['query_count'], 9)
        self.assertEqual(result[2]['historical_candidate_fixture_diagnostic']['required_evidence_hits'], 2)

    def test_unapproved_or_different_decision_cannot_qualify(self):
        for field, value in (('decision', 'request-changes'), ('proposal_sha256', '0'*64),
                             ('proposal', {}), ('reviewer_id', ''), ('release_approval', True)):
            with self.subTest(field=field):
                result = self.altered('retrieval_approval', lambda d: d.update({field:value}))
                self.assertEqual(result[0], 'failed')

    def test_missing_approval_artifact_fails(self):
        self.candidate = copy.deepcopy(self.candidate)
        del self.candidate['artifacts']['retrieval_approval']
        self.assertEqual(self.evaluate()[0], 'failed')

    def test_production_miss_or_omitted_case_fails_even_if_fixture_passes(self):
        for mutate in (lambda d: d['production_queries'][0].update(returned_chunk_ids=[]),
                       lambda d: d['production_queries'].pop(),
                       lambda d: d['production_queries'][0].update(returned_chunk_ids=['unsigned'])):
            with self.subTest(mutation=mutate):
                self.assertEqual(self.altered('approved_retrieval', mutate)[0], 'failed')

    def test_fixture_miss_or_omitted_case_fails_even_if_production_passes(self):
        for mutate in (lambda d: d['fixture_queries'][0].update(returned_document_ids=[]),
                       lambda d: d['fixture_queries'].pop()):
            with self.subTest(mutation=mutate):
                self.assertEqual(self.altered('approved_retrieval', mutate)[0], 'failed')

    def test_fixture_forbidden_result_after_rank_three_is_still_a_violation(self):
        report = read_artifact(ROOT, self.candidate, 'approved_retrieval')
        rows = report['fixture_queries']
        rows[0]['returned_document_ids'] = [
            'di-rag-doc-permanent-residence-language', 'di-rag-doc-terse-danish-exam',
            'di-rag-doc-evidence-boundary', 'di-rag-doc-citizenship-language']
        metrics = fixture_metrics(ROOT, rows)
        self.assertGreater(metrics['forbidden_result_violations'], 0)

    def test_changed_model_or_implementation_cannot_reuse_measurement(self):
        changes = (lambda d: d['fixture_index'].update(vector_dimensions=1),
                   lambda d: d['fixture_index']['embedding_model_identity'].update(digest='other'),
                   lambda d: d.update(implementation_sha256={}),
                   lambda d: d.update(fixture_dataset_sha256={}),
                   lambda d: d.update(proposal_sha256='0'*64),
                   lambda d: d.update(execution_error_count=1))
        for mutate in changes:
            with self.subTest(mutation=mutate):
                self.assertEqual(self.altered('approved_retrieval', mutate)[0], 'failed')

    def test_malformed_nested_evidence_is_structured_failure(self):
        self.assertEqual(self.altered('approved_retrieval', lambda d: d.update(fixture_index=None))[0], 'failed')

    def test_quality_threshold_drift_cannot_silently_qualify(self):
        self.quality['thresholds']['retrieval']['critical_case_recall_at_3_min'] = .5
        self.assertEqual(self.evaluate()[0], 'failed')
