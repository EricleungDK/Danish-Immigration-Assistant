import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from danish_rag.candidate_answer_evidence import validate_candidate_answer
from tests.evidence_rebind import rebind_implementation_hashes

ROOT = Path(__file__).resolve().parents[1]


class CandidateAnswerEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.quality = json.loads((ROOT / 'config/evaluation-quality-bar.json').read_text())
        self.runtime = json.loads((ROOT / 'config/runtime-policy.json').read_text())
        # ADR 0001 (docs/adr/0001-requalify-per-release-not-per-commit.md): committed
        # implementation hashes are stale after refactors, so the chain is verified with
        # bindings recomputed from the current files.
        self.candidate = self.prepare(self.root, rebind=True)

    def prepare(self, root, *, rebind):
        candidate = {'release_dir': 'data/knowledge_releases/kr-2026-09-05.1', 'artifacts': {}}
        paths = ['config/evaluation-quality-bar.json', self.quality['evaluation_set']['path'],
                 candidate['release_dir'] + '/manifest.json']
        evidence = []
        for name, path in [('final_answer', 'docs/progress/issue-51-reviewed-candidate-replay.json'),
                           ('final_answer_origin', 'docs/progress/issue-51-final-answer-remediation.json')]:
            paths.append(path)
            evidence.append(path)
            candidate['artifacts'][name] = {'path': path}
        origin = json.loads((ROOT / paths[-1]).read_text())
        paths.extend(origin['implementation_sha256'])
        for path in paths:
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, target)
        if rebind:
            rebind_implementation_hashes(root, evidence)
        for item in candidate['artifacts'].values():
            item['sha256'] = hashlib.sha256((root / item['path']).read_bytes()).hexdigest()
        return candidate

    def test_committed_evidence_is_stale_after_implementation_changes(self):
        # ADR 0001: the committed hashes record the qualified candidate, not the current tree.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = self.prepare(root, rebind=False)
            failures = validate_candidate_answer(root, self.quality, self.runtime, candidate)
            self.assertTrue(any('implementation changed' in item for item in failures), failures)

    def result(self):
        return validate_candidate_answer(self.root, self.quality, self.runtime, self.candidate)

    def mutate(self, change, artifact='final_answer'):
        item = self.candidate['artifacts'][artifact]
        path = self.root / item['path']
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))
        item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()

    def test_current_reviewed_candidate_public_chain_passes(self):
        self.assertEqual(self.result(), [])

    def test_hash_tamper_rejected(self):
        self.candidate['artifacts']['final_answer']['sha256'] = 'a' * 64
        self.assertTrue(self.result())

    def test_capture_binding_tamper_rejected_even_with_new_artifact_hash(self):
        self.mutate(lambda d: d['private_evidence'].update(capture_sha256='a'*64))
        self.assertTrue(self.result())

    def test_wrong_candidate_identity_rejected(self):
        self.mutate(lambda d: d['identity'].update(corpus_id='other'))
        self.assertTrue(self.result())

    def test_dataset_tamper_rejected(self):
        self.mutate(lambda d: d['dataset'].update(sha256='a'*64))
        self.assertTrue(self.result())

    def test_missing_human_review_rejected(self):
        self.mutate(lambda d: d['adjudications'].update(independent_human_case_count=0))
        self.assertTrue(self.result())

    def test_pass_label_cannot_hide_metric_failure(self):
        self.mutate(lambda d: d['metrics']['required_fact_coverage'].update(covered_count=30, observed=0.5))
        self.assertTrue(self.result())

    def test_metric_ratio_cannot_be_forged(self):
        self.mutate(lambda d: d['metrics']['required_fact_coverage'].update(covered_count=30))
        self.assertTrue(self.result())

    def test_threshold_cannot_be_weakened_in_report(self):
        self.mutate(lambda d: d['metrics']['required_fact_coverage'].update(threshold=0.5))
        self.assertTrue(self.result())

    def test_stale_implementation_rejected(self):
        with (self.root / 'danish_rag/answer_pipeline.py').open('a') as stream:
            stream.write('\n# changed\n')
        self.assertTrue(self.result())

    def test_origin_cannot_claim_replay_as_live_generation(self):
        self.mutate(lambda d: d['execution'].update(live_provider_calls=False), 'final_answer_origin')
        self.assertTrue(self.result())

    def test_malformed_input_fails_closed(self):
        self.candidate['artifacts']['final_answer'] = None
        self.assertTrue(self.result())

    def test_missing_metric_fails_closed(self):
        self.mutate(lambda d: d['metrics'].pop('citation_correctness'))
        self.assertTrue(self.result())
