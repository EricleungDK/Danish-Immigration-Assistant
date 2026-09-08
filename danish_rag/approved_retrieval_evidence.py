"""Owner-approved production retrieval and separate mandatory fixture checks.

The historical candidate/fixture mismatch remains an immutable diagnostic. This
contract implements the explicit September owner decision; it is not a fallback
that can infer approval from passing measurements.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .candidate_release_evidence import read_artifact, safe_path
from .candidate_retrieval_evidence import DATASETS, _frozen, _provenance, evaluate_candidate_retrieval
from .evidence_integrity import sha256_file, utc_now_seconds
from .knowledge_release import default_data_dir, verify_knowledge_release
from .retrieval import HybridRetriever, _attach_source_metadata, inspect_embedding_model
from .retrieval_benchmark import _is_eligible_for_query, run_hybrid_retrieval_comparison
from .source_freshness import assess_source_freshness

IMPLEMENTATIONS = ('danish_rag/approved_retrieval_evidence.py',
                   'danish_rag/candidate_retrieval_evidence.py',
                   'danish_rag/retrieval.py', 'danish_rag/retrieval_benchmark.py',
                   'danish_rag/source_freshness.py')
HISTORICAL_FAILURES = {
    'candidate required-evidence Recall@3 below threshold',
    'candidate critical-case Recall@3 not evaluable: approved case mapping missing',
}


def approved_cases(root: Path, candidate: dict, thresholds: dict) -> list[dict]:
    proposal = read_artifact(root, candidate, 'retrieval_proposal')
    decision = read_artifact(root, candidate, 'retrieval_approval')
    if (decision.get('schema_version') != 'release-human-decisions-v1'
        or decision.get('decision') != 'approve-proposed-benchmark'
        or not isinstance(decision.get('reviewer_id'), str)
        or not decision['reviewer_id'].strip()
        or not decision.get('reviewed_at_utc')
        or decision.get('proposal') != proposal
        or decision.get('proposal_sha256') != candidate['artifacts']['retrieval_proposal']['sha256']
        or decision.get('release_approval') is not False):
        raise ValueError('explicit owner approval does not match the proposed benchmark')
    if proposal.get('schema_version') != 'release-human-decisions-proposal-v1':
        raise ValueError('unsupported approved retrieval proposal')
    if proposal['candidate'] != Path(candidate['release_dir']).name:
        raise ValueError('approved retrieval candidate differs')
    if proposal['thresholds'] != {key: thresholds[key] for key in proposal['thresholds']}:
        raise ValueError('approved retrieval thresholds differ from quality bar')
    if set(proposal['thresholds']) != set(thresholds) - {'retrieval_failure_may_be_masked_by_generation'}:
        raise ValueError('approved retrieval thresholds incomplete')
    cases = proposal['cases']
    # This schema is the exact 15-case/all-critical proposal approved by the owner.
    if (len(cases) != 15 or len({c['id'] for c in cases}) != 15
        or any(c.get('critical') is not True or not c.get('required_document_ids') for c in cases)):
        raise ValueError('approved critical-case mapping incomplete')
    return cases


def fixture_metrics(root: Path, rows: list[dict]) -> dict:
    queries, fixtures = _frozen(root)
    if [row['id'] for row in rows] != [q['id'] for q in queries]:
        raise ValueError('all nine frozen fixture cases are required')
    hits = count = blocked = forbidden = 0
    for query, row in zip(queries, rows):
        returned = row['returned_document_ids']
        if (not isinstance(returned, list) or len(returned) > len(fixtures)
            or len(set(returned)) != len(returned) or any(i not in fixtures for i in returned)):
            raise ValueError('invalid fixture eligible documents')
        required = {i for i in query['required_document_ids'] if _is_eligible_for_query(fixtures[i], query)}
        count += bool(required)
        hits += bool(required) and required <= set(returned[:3])
        blocked += sum(not _is_eligible_for_query(fixtures[i], query) for i in returned)
        forbidden += len(set(returned) & set(query['forbidden_document_ids']))
    return dict(query_count=len(rows), required_evidence_query_count=count,
                required_evidence_hits=hits, required_evidence_recall_at_3=hits/count if count else None,
                blocked_source_violations=blocked, forbidden_result_violations=forbidden)


def production_metrics(cases: list[dict], rows: list[dict], release: dict, evaluated_at: str) -> dict:
    if [row['id'] for row in rows] != [case['id'] for case in cases]:
        raise ValueError('approved production case coverage differs')
    sources = {source['source_id']: source for source in release['manifest']['sources']}
    documents = {d['document_id']: _attach_source_metadata(d, sources[d['source_id']]) for d in release['documents']}
    source_ids = {d['source_document_id'] for d in documents.values()}
    hits = blocked = 0
    for case, row in zip(cases, rows):
        if not set(case['required_document_ids']) <= source_ids:
            raise ValueError('approved required source missing from signed corpus')
        returned = row['returned_chunk_ids']
        if (not isinstance(returned, list) or len(returned) > 3
            or len(set(returned)) != len(returned) or any(i not in documents for i in returned)):
            raise ValueError('production result is not a signed top-three chunk')
        eligible_sources = set()
        for chunk_id in returned:
            document = documents[chunk_id]
            if assess_source_freshness(document, evaluated_at_utc=evaluated_at).answer_eligible:
                eligible_sources.add(document['source_document_id'])
            else:
                blocked += 1
        hits += set(case['required_document_ids']) <= eligible_sources
    return dict(required_evidence_query_count=len(cases), required_evidence_hits=hits,
                required_evidence_recall_at_3=hits/len(cases), critical_case_count=len(cases),
                critical_case_recall_at_3=hits/len(cases), blocked_source_violations=blocked,
                forbidden_result_violations=0)


def evaluate_approved_retrieval(root: Path, quality: dict, runtime: dict, candidate: dict) -> tuple:
    thresholds = quality['thresholds']['retrieval']
    observed: dict[str, Any] = {}
    evidence = []
    failures = []
    try:
        cases = approved_cases(root, candidate, thresholds)
        historical = evaluate_candidate_retrieval(root, quality, runtime, candidate)
        # Only the two explicitly superseded comparisons stop blocking. Integrity,
        # grounded-intent, safety and execution failures remain blocking.
        failures.extend(f for f in historical[5] if f not in HISTORICAL_FAILURES)
        evidence.extend(historical[4])
        observed['historical_candidate_fixture_diagnostic'] = historical[2]
        report = read_artifact(root, candidate, 'approved_retrieval')
        for name in ('retrieval_proposal', 'retrieval_approval', 'approved_retrieval'):
            evidence.append(candidate['artifacts'][name])
        if report['schema_version'] != 'approved-production-retrieval-v1' or report['mode'] != 'live-local-embedding':
            raise ValueError('approved retrieval report execution mode differs')
        if report['proposal_sha256'] != candidate['artifacts']['retrieval_proposal']['sha256']:
            raise ValueError('measurement proposal binding differs')
        if report['approval_sha256'] != candidate['artifacts']['retrieval_approval']['sha256']:
            raise ValueError('measurement approval binding differs')
        if report['implementation_sha256'] != {p: sha256_file(root/p) for p in IMPLEMENTATIONS}:
            raise ValueError('approved retrieval implementation changed')
        if report['fixture_dataset_sha256'] != {p: sha256_file(root/p) for p in DATASETS}:
            raise ValueError('frozen fixture dataset changed')
        release = verify_knowledge_release(safe_path(root, candidate['release_dir']),
                                           trust_root_path=safe_path(root, candidate['trust_root_path']))
        _provenance(root, report, candidate, release['manifest'], quality, runtime)
        expected_index = read_artifact(root, candidate, 'retrieval')['provenance']['index_metadata']
        if report['provenance']['index_metadata'] != expected_index:
            raise ValueError('approved production index differs from reviewed candidate')
        fixture_index = report['fixture_index']
        if (fixture_index['corpus_fixture_identity']['corpus_sha256'] != sha256_file(root/DATASETS[2])
            or fixture_index['embedding_model'] != runtime['models']['embedding']['initial_supported']
            or fixture_index['vector_dimensions'] != expected_index['vector_dimensions']
            or fixture_index['embedding_model_identity'] != expected_index['embedding_model_identity']):
            raise ValueError('fixture corpus or embedding identity differs')
        if report['execution_error_count'] != 0:
            raise ValueError('retrieval execution errors occurred')
        observed.update(production_metrics(cases, report['production_queries'], release, report['generated_at_utc']))
        # Re-evaluate freshness now too: old passing evidence cannot authorize a
        # currently blocked source merely because collection happened earlier.
        current = production_metrics(cases, report['production_queries'], release, utc_now_seconds())
        if current['blocked_source_violations']:
            failures.append('production sources are no longer answer-eligible')
        fixture = fixture_metrics(root, report['fixture_queries'])
        observed['mandatory_fixture_regressions'] = fixture
        for label, metrics in (('production', observed), ('fixture', fixture)):
            if metrics['required_evidence_recall_at_3'] is None or metrics['required_evidence_recall_at_3'] < thresholds['required_evidence_recall_at_3_min']:
                failures.append(f'{label} required-evidence Recall@3 below threshold')
            for key in ('blocked_source_violations', 'forbidden_result_violations'):
                if metrics[key] > thresholds[key+'_max']:
                    failures.append(f'{label} {key} exceeds threshold')
        if observed['critical_case_recall_at_3'] < thresholds['critical_case_recall_at_3_min']:
            failures.append('production critical-case Recall@3 below threshold')
    except (AttributeError, KeyError, TypeError, ValueError, OSError, RuntimeError, ZeroDivisionError) as exc:
        failures.append(f'approved retrieval evidence invalid: {exc}')
    return ('failed' if failures else 'passed',
            'Owner-approved production corpus check and separate mandatory frozen-fixture regression check.',
            observed, thresholds, evidence, failures)


class _BenchmarkEmbeddingClient:
    """Run the legacy fixture harness through the same inspected local provider."""

    def __init__(self, provider: Any):
        self.provider = provider
        self.endpoint = provider.endpoint

    def show_model(self, model: str) -> dict:
        return {'identity': inspect_embedding_model(self.provider, model)}

    def embed(self, model: str, text: str) -> dict:
        return {'embedding': self.provider.embed(model, text)}


def collect(root: Path, candidate: dict, retriever: HybridRetriever) -> dict:
    quality = json.loads((root/'config/evaluation-quality-bar.json').read_text())
    cases = approved_cases(root, candidate, quality['thresholds']['retrieval'])
    if sha256_file(retriever.active_release['manifest_path']) != candidate['manifest_sha256']:
        raise ValueError('installed candidate differs from approved release')
    rows = [{'id': c['id'], 'returned_chunk_ids': [d['document_id'] for d in retriever.retrieve(c['prompt'], limit=3)]} for c in cases]
    fixture = run_hybrid_retrieval_comparison(root/DATASETS[2], root/DATASETS[0], root/DATASETS[1],
                                             policy_path=root/'config/runtime-policy.json',
                                             client=_BenchmarkEmbeddingClient(retriever.embedding_provider))
    model = retriever.dense_index['metadata']['embedding_model']
    if inspect_embedding_model(retriever.embedding_provider, model) != retriever.dense_index['metadata']['embedding_model_identity']:
        raise ValueError('embedding model changed during collection')
    return dict(schema_version='approved-production-retrieval-v1', mode='live-local-embedding',
                generated_at_utc=utc_now_seconds(), execution_error_count=0,
                proposal_sha256=candidate['artifacts']['retrieval_proposal']['sha256'],
                approval_sha256=candidate['artifacts']['retrieval_approval']['sha256'],
                implementation_sha256={p: sha256_file(root/p) for p in IMPLEMENTATIONS},
                fixture_dataset_sha256={p: sha256_file(root/p) for p in DATASETS},
                production_queries=rows,
                fixture_queries=[{'id': q['query_id'], 'returned_document_ids': q['eligible_result_ids']} for q in fixture['candidates']['hybrid']['queries']],
                fixture_index=fixture['index'],
                provenance={'manifest_sha256':candidate['manifest_sha256'],
                            'retrieval_implementation_sha256':sha256_file(root/'danish_rag/retrieval.py'),
                            'index_metadata':retriever.dense_index['metadata']})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--data-dir', default=default_data_dir())
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(args.root)
    candidate = json.loads((root/'config/release-qualification.json').read_text())['candidate_evidence']
    retriever = HybridRetriever.from_data_dir(args.data_dir, trust_root_path=safe_path(root, candidate['trust_root_path']))
    report = collect(root, candidate, retriever)
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')


if __name__ == '__main__':
    main()
