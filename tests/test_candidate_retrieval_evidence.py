"""Candidate evidence must never inherit historical fixture passing scores."""
import json
from pathlib import Path
import tempfile
import unittest

from danish_rag.candidate_retrieval_evidence import DATASETS, baseline_metrics, load_artifact, safe_path
from danish_rag.evidence_integrity import sha256_file

ROOT = Path(__file__).resolve().parents[1]


class CandidateRetrievalEvidenceTests(unittest.TestCase):
    def report(self):
        from danish_rag.candidate_retrieval_evidence import _frozen
        from danish_rag.retrieval_benchmark import _is_eligible_for_query
        queries, fixtures = _frozen(ROOT)
        return {'retrieval_limit': 3,
                'dataset_sha256': {p: sha256_file(ROOT / p) for p in DATASETS},
                'queries': [{'id': q['id'], 'required_document_ids': sorted(
                    i for i in q['required_document_ids'] if _is_eligible_for_query(fixtures[i], q)),
                    'returned_source_document_ids': [], 'blocked_source_violations': 0,
                    'execution_error_count': 0} for q in queries]}

    def test_missing_fixture_documents_are_misses_not_excluded(self):
        metrics = baseline_metrics(ROOT, self.report())
        self.assertEqual(metrics['required_evidence_query_count'], 7)
        self.assertEqual(metrics['required_evidence_recall_at_3'], 0)
        self.assertIsNone(metrics['critical_case_recall_at_3'])

    def test_all_required_sources_needed_for_credit(self):
        report = self.report()
        for row in report['queries']:
            row['returned_source_document_ids'] = row['required_document_ids']
        self.assertEqual(baseline_metrics(ROOT, report)['required_evidence_recall_at_3'], 1)
        report['queries'][0]['required_document_ids'] = []
        with self.assertRaisesRegex(ValueError, 'required documents changed'):
            baseline_metrics(ROOT, report)

    def test_omitted_query_rejected(self):
        report = self.report()
        report['queries'].pop()
        with self.assertRaisesRegex(ValueError, 'query coverage'):
            baseline_metrics(ROOT, report)

    def test_dataset_change_rejected(self):
        report = self.report()
        report['dataset_sha256'][DATASETS[0]] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'dataset mismatch'):
            baseline_metrics(ROOT, report)

    def test_more_than_three_results_rejected(self):
        report = self.report()
        report['queries'][0]['returned_source_document_ids'] = ['a'] * 4
        with self.assertRaisesRegex(ValueError, 'top-three'):
            baseline_metrics(ROOT, report)

    def test_artifact_hash_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'report.json').write_text('{}')
            candidate = {'artifacts': {'retrieval': {'path': 'report.json', 'sha256': '0' * 64}}}
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                load_artifact(root, candidate, 'retrieval')

    def test_reference_cannot_escape_by_symlink_or_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'outside').symlink_to('/tmp')
            for reference in ('../other', '/tmp/other', 'outside/other'):
                with self.subTest(reference=reference), self.assertRaises(ValueError):
                    safe_path(root, reference)

    def test_current_signed_candidate_does_not_inherit_old_passing_score(self):
        from danish_rag.candidate_retrieval_evidence import evaluate_candidate_retrieval
        paths = {'retrieval': 'docs/progress/issue-51-retrieval-remediation.json',
                 'retrieval_baseline': 'docs/progress/issue-51-candidate-retrieval-baseline.json'}
        candidate = {'release_dir': 'data/knowledge_releases/kr-2026-09-05.1',
                     'manifest_sha256': sha256_file(ROOT / 'data/knowledge_releases/kr-2026-09-05.1/manifest.json'),
                     'trust_root_path': 'config/trust_roots/project-release-key-v2.json',
                     'artifacts': {key: {'path': path, 'sha256': sha256_file(ROOT / path)} for key, path in paths.items()}}
        quality = json.loads((ROOT / 'config/evaluation-quality-bar.json').read_text())
        runtime = json.loads((ROOT / 'config/runtime-policy.json').read_text())
        result = evaluate_candidate_retrieval(ROOT, quality, runtime, candidate)
        self.assertEqual(result[0], 'failed')
        self.assertIn('candidate required-evidence Recall@3 below threshold', result[5])
        self.assertEqual(result[2]['required_evidence_query_count'], 7)
        self.assertEqual(result[2]['required_evidence_hits'], 2)
        candidate['manifest_sha256'] = '0' * 64
        result = evaluate_candidate_retrieval(ROOT, quality, runtime, candidate)
        self.assertEqual(result[0], 'failed')
        self.assertIn('candidate manifest hash mismatch', result[5][0])
