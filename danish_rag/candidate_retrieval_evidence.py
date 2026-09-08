"""Candidate retrieval evidence: preserve frozen expectations and fail closed."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .evidence_integrity import reject_duplicate_json_object, sha256_file, utc_now_seconds
from .knowledge_release import verify_knowledge_release, default_data_dir
from .retrieval_benchmark import _is_eligible_for_query

DATASETS = ('data/retrieval_benchmark/evaluation-queries.json',
            'data/retrieval_benchmark/dense-evaluation-queries.json',
            'data/retrieval_benchmark/corpus-fixtures.json')
GROUNDED = 'data/evaluation/grounded-flexibility-v0.1-candidate.json'


def safe_path(root: Path, reference: str) -> Path:
    if not isinstance(reference, str) or not reference or Path(reference).is_absolute():
        raise ValueError('candidate reference must be repository-relative')
    path = (root / reference).resolve()
    if '..' in Path(reference).parts or not path.is_relative_to(root.resolve()):
        raise ValueError('candidate reference escapes repository')
    return path


def load_artifact(root: Path, candidate: dict, name: str) -> tuple[dict, dict]:
    binding = candidate['artifacts'][name]
    path = safe_path(root, binding['path'])
    digest = sha256_file(path)
    if digest != binding['sha256']:
        raise ValueError(f'{name} artifact hash mismatch')
    return json.loads(path.read_text(), object_pairs_hook=reject_duplicate_json_object), {
        'path': binding['path'], 'sha256': digest}


def _frozen(root: Path) -> tuple[list, dict]:
    queries = json.loads((root / DATASETS[0]).read_text()) + json.loads((root / DATASETS[1]).read_text())
    corpus = json.loads((root / DATASETS[2]).read_text())
    return queries, {row['id']: row for row in corpus}


def _provenance(root: Path, report: dict, candidate: dict, manifest: dict,
                quality_bar: dict, runtime_policy: dict) -> None:
    p = report['provenance']
    if p['manifest_sha256'] != candidate['manifest_sha256']:
        raise ValueError('retrieval manifest binding mismatch')
    if p['retrieval_implementation_sha256'] != sha256_file(root / 'danish_rag/retrieval.py'):
        raise ValueError('retrieval implementation changed')
    m = p['index_metadata']
    for key, expected in {
        'knowledge_release_id': manifest['knowledge_release_id'], 'corpus_identity': manifest['corpus_id'],
        'corpus_schema_version': manifest['corpus_schema_version'],
        'content_unit_schema_version': manifest['content_unit_schema_version'],
        'embedding_model': runtime_policy['models']['embedding']['initial_supported'],
        'vector_dimensions': quality_bar['retrieval_baseline']['vector_dimensions'], 'retrieval': 'hybrid',
    }.items():
        if m.get(key) != expected:
            raise ValueError(f'retrieval index {key} mismatch')
    identity = m['embedding_model_identity']
    if not identity.get('digest') or not identity.get('identity_fingerprint_sha256') or 'embedding' not in identity.get('capabilities', []):
        raise ValueError('embedding identity incomplete')
    if runtime_policy['models']['embedding']['supported_for_production'] is not True:
        raise ValueError('embedding not production supported')


def baseline_metrics(root: Path, report: dict) -> dict:
    """Recompute all-required-source hits against unchanged fixture expectations."""
    queries, fixtures = _frozen(root)
    if report['dataset_sha256'] != {name: sha256_file(root / name) for name in DATASETS}:
        raise ValueError('frozen benchmark dataset mismatch')
    rows = report['queries']
    if report['retrieval_limit'] != 3 or [r['id'] for r in rows] != [q['id'] for q in queries]:
        raise ValueError('frozen benchmark query coverage or limit mismatch')
    hits = count = blocked = forbidden = errors = 0
    for query, row in zip(queries, rows):
        required = {i for i in query['required_document_ids'] if _is_eligible_for_query(fixtures[i], query)}
        returned = row['returned_source_document_ids']
        if not isinstance(returned, list) or len(returned) > 3 or not all(isinstance(i, str) for i in returned):
            raise ValueError('invalid top-three source IDs')
        if row['required_document_ids'] != sorted(required):
            raise ValueError('frozen benchmark required documents changed')
        count += bool(required)
        hits += bool(required) and required <= set(returned)
        for key in ('blocked_source_violations', 'execution_error_count'):
            if type(row[key]) is not int or row[key] < 0:
                raise ValueError('invalid retrieval violation/error count')
        blocked += row['blocked_source_violations']
        errors += row['execution_error_count']
        forbidden += len(set(returned) & set(query['forbidden_document_ids']))
    return {'required_evidence_query_count': count, 'required_evidence_hits': hits,
            'required_evidence_recall_at_3': hits / count if count else None,
            'blocked_source_violations': blocked, 'forbidden_result_violations': forbidden,
            'execution_error_count': errors, 'critical_case_recall_at_3': None,
            'critical_case_evidence_status': 'not_evaluable: frozen fixture benchmark has no approved critical-case mapping'}


def evaluate_candidate_retrieval(root: Path, quality_bar: dict, runtime_policy: dict, candidate: dict) -> tuple:
    thresholds = dict(quality_bar['thresholds']['retrieval'])
    observed: dict[str, Any] = {}
    evidence: list[dict] = []
    failures: list[str] = []
    try:
        release_dir = safe_path(root, candidate['release_dir'])
        if sha256_file(release_dir / 'manifest.json') != candidate['manifest_sha256']:
            raise ValueError('candidate manifest hash mismatch')
        manifest = verify_knowledge_release(release_dir, trust_root_path=safe_path(root, candidate['trust_root_path']))['manifest']
        report, ref = load_artifact(root, candidate, 'retrieval')
        evidence.append(ref)
        _provenance(root, report, candidate, manifest, quality_bar, runtime_policy)
        dataset = json.loads((root / GROUNDED).read_text())
        if report['dataset_sha256'] != sha256_file(root / GROUNDED) or report['case_count'] != len(dataset['cases']) or report['retrieval_limit'] != 3:
            raise ValueError('grounded retrieval dataset/count/limit mismatch')
        if report['provenance']['collector_implementation_sha256'] != sha256_file(root / 'danish_rag/grounded_flexibility_retrieval.py'):
            raise ValueError('grounded retrieval collector changed')
        required: dict[str, int] = {}
        for case in dataset['cases']:
            for intent in case['expected_intents']:
                key = intent['intent_id']
                required[key] = required.get(key, 0) + len(set(intent['required_document_ids']))
        if set(required) != set(report['by_intent']):
            raise ValueError('grounded retrieval intent coverage mismatch')
        for key, count in required.items():
            row = report['by_intent'][key]
            if type(row['covered']) is not int or not 0 <= row['covered'] <= count or row['required'] != count:
                raise ValueError('grounded retrieval counts invalid')
            ratio = row['covered'] / count
            if row['observed'] != ratio or ratio < dataset['thresholds']['per_intent_evidence_coverage_min']:
                failures.append(f'grounded intent coverage failed: {key}')
        if report['execution_error_count'] != 0 or report['blocked_source_violations'] != 0:
            failures.append('grounded retrieval errors or blocked sources')
        observed['grounded_intent_coverage'] = report['by_intent']
        baseline, ref = load_artifact(root, candidate, 'retrieval_baseline')
        evidence.append(ref)
        if baseline['schema_version'] != 'candidate-retrieval-baseline-v1':
            raise ValueError('candidate retrieval baseline schema mismatch')
        _provenance(root, baseline, candidate, manifest, quality_bar, runtime_policy)
        if baseline['provenance']['collector_implementation_sha256'] != sha256_file(root / 'danish_rag/candidate_retrieval_evidence.py'):
            raise ValueError('candidate baseline collector changed')
        observed.update(baseline_metrics(root, baseline))
        if observed['required_evidence_recall_at_3'] is None or observed['required_evidence_recall_at_3'] < thresholds['required_evidence_recall_at_3_min']:
            failures.append('candidate required-evidence Recall@3 below threshold')
        for key in ('blocked_source_violations', 'forbidden_result_violations'):
            if observed[key] > thresholds[key + '_max']:
                failures.append(f'candidate {key} exceeds threshold')
        if observed['execution_error_count']:
            failures.append('candidate retrieval execution errors')
        failures.append('candidate critical-case Recall@3 not evaluable: approved case mapping missing')
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        failures.append(f'candidate retrieval evidence invalid: {exc}')
    return ('failed' if failures else 'passed',
            'Current signed-candidate retrieval evaluated separately from historical fixture scores.',
            observed, thresholds, evidence, failures)


def collect_baseline(root: Path, retriever: Any) -> dict:
    from .source_freshness import assess_source_freshness
    queries, fixtures = _frozen(root)
    now = utc_now_seconds()
    rows = []
    for query in queries:
        error = 0
        try:
            results = retriever.retrieve(query['query_text'], limit=3)
        except Exception:
            results, error = [], 1
        eligible = [r for r in results if assess_source_freshness(r, evaluated_at_utc=now).answer_eligible]
        rows.append({'id': query['id'],
                     'required_document_ids': sorted(i for i in query['required_document_ids'] if _is_eligible_for_query(fixtures[i], query)),
                     'returned_source_document_ids': [r.get('source_document_id', r['document_id']) for r in eligible],
                     'blocked_source_violations': len(results) - len(eligible), 'execution_error_count': error})
    return {'schema_version': 'candidate-retrieval-baseline-v1', 'generated_at_utc': now,
            'dataset_sha256': {name: sha256_file(root / name) for name in DATASETS},
            'retrieval_limit': 3, 'queries': rows,
            'provenance': {'manifest_sha256': sha256_file(retriever.active_release['manifest_path']),
                           'retrieval_implementation_sha256': sha256_file(root / 'danish_rag/retrieval.py'),
                           'collector_implementation_sha256': sha256_file(__file__),
                           'dense_index_sha256': sha256_file(retriever.index_dir / 'dense-index.json'),
                           'index_metadata': retriever.dense_index['metadata']}}


def main() -> None:
    from .retrieval import HybridRetriever
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--data-dir', default=default_data_dir())
    parser.add_argument('--trust-root-path', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    retriever = HybridRetriever.from_data_dir(args.data_dir, trust_root_path=args.trust_root_path)
    report = collect_baseline(Path(args.root), retriever)
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps(baseline_metrics(Path(args.root), report), sort_keys=True))


if __name__ == '__main__':
    main()
