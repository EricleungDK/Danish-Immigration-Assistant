"""Validate public, hash-bound candidate answer aggregates without private replay."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

_IMPLEMENTATIONS = {
    'danish_rag/answer_pipeline.py', 'danish_rag/answer_verification.py',
    'danish_rag/final_answer_evaluation.py', 'danish_rag/retrieval.py',
}


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _read(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('evidence path escapes repository')
    return path.read_bytes()


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError('invalid evidence count')
    return value


def validate_candidate_answer(root: Path, quality_bar: dict, runtime_policy: dict,
                              candidate: dict) -> list[str]:
    """Check report-level provenance and arithmetic; never infer a private replay."""
    try:
        return _validate(root, quality_bar, runtime_policy, candidate)
    except (KeyError, TypeError, ValueError, OSError, AttributeError, ZeroDivisionError) as exc:
        return [f'candidate answer evidence malformed or unavailable ({type(exc).__name__})']


def _validate(root, quality_bar, runtime_policy, candidate):
    failures = []

    def require(condition, label):
        if not condition:
            failures.append('candidate answer ' + label)

    reports = []
    for name in ('final_answer', 'final_answer_origin'):
        artifact = candidate['artifacts'][name]
        raw = _read(root, artifact['path'])
        require(_sha(artifact['sha256']) and hashlib.sha256(raw).hexdigest() == artifact['sha256'], name + 'artifact hash differs')
        reports.append(json.loads(raw))
    reviewed, origin = reports
    dataset_raw = _read(root, quality_bar['evaluation_set']['path'])
    dataset = json.loads(dataset_raw)
    quality_raw = _read(root, 'config/evaluation-quality-bar.json')
    require(json.loads(quality_raw) == quality_bar, 'quality bar argument differs from current file')
    manifest = json.loads(_read(root, candidate['release_dir'] + '/manifest.json'))
    corpus = manifest['knowledge_release_id']
    case_count = len(dataset['cases'])
    require(case_count == quality_bar['evaluation_set']['case_count'] == 20, 'dataset case count differs')
    expected_dataset = {
        'dataset_id': dataset['dataset_id'], 'version': dataset['version'],
        'sha256': hashlib.sha256(dataset_raw).hexdigest(), 'case_count': case_count,
        'uses_production_user_conversation_data': False,
    }
    expected_quality = {'quality_bar_id': quality_bar['quality_bar_id'],
                        'version': quality_bar['version'],
                        'sha256': hashlib.sha256(quality_raw).hexdigest()}
    provider = runtime_policy['providers']['initial']
    minimum = tuple(int(x) for x in provider['minimum_version'].split('.'))
    for report, mode, live in ((origin, 'live-ollama', True), (reviewed, 'captured-live-ollama', False)):
        require(report['schema_version'] == 'final-answer-evaluation-v1', 'schema differs')
        require(report['dataset'] == expected_dataset, 'dataset binding differs')
        require(report['quality_bar'] == expected_quality, 'quality bar binding differs')
        execution = report['execution']
        for key, value in {'case_count': case_count, 'completed_count': case_count,
                           'error_count': 0, 'not_evaluable_count': 0,
                           'answer_case_execution_count': 10}.items():
            require(_count(execution[key]) == value, 'execution ' + key + ' differs')
        require(execution['mode'] == mode and execution['live_provider_calls'] is live, 'execution provenance differs')
        identity = report['identity']
        require(identity['corpus_id'] == corpus, 'corpus identity differs')
        require(identity['provider_id'] == provider['id'] == 'ollama', 'provider identity differs')
        require(identity['model'] == runtime_policy['models']['generation']['initial'], 'model identity differs')
        version = tuple(int(x) for x in identity['provider_version'].split('.'))
        require(len(version) == len(minimum) and version >= minimum, 'provider version below minimum')
    require(reviewed['identity'] == origin['identity'], 'reviewed identity differs from origin')
    require(reviewed['scope'] == 'explicit-candidate-only', 'reviewed scope differs')
    require(reviewed['strict_passed'] is True and reviewed['threshold_failures'] == [], 'reviewed gates not strict')
    for key in ('capture_sha256', 'report_sha256'):
        value = origin['private_evidence'][key]
        require(_sha(value) and reviewed['private_evidence'][key] == value, 'private packet binding differs')
    for key in ('human_adjudications_sha256', 'reviewed_replay_report_sha256'):
        require(_sha(reviewed['private_evidence'][key]), 'review hash invalid')
    adjudications = reviewed['adjudications']
    require(adjudications['provided'] is True and _sha(adjudications['sha256']), 'adjudication binding missing')
    for key, value in {'case_count': 16, 'independent_human_case_count': 10,
                       'automated_workflow_case_count': 6}.items():
        require(_count(adjudications[key]) == value, 'adjudication count differs')
    hashes = origin['implementation_sha256']
    require(set(hashes) == _IMPLEMENTATIONS, 'implementation inventory differs')
    for path, sha in hashes.items():
        require(_sha(sha) and hashlib.sha256(_read(root, path)).hexdigest() == sha, 'implementation changed: ' + path)
    require(origin['implementation_unchanged_during_capture'] is True, 'implementation changed during capture')
    require(origin['generation_model_digest_unchanged'] is True and _sha(origin['generation_model_digest']), 'generation model provenance missing')

    metrics = reviewed['metrics']
    thresholds = quality_bar['thresholds']['final_answer']
    expected_metrics = {
        'required_fact_coverage': ('covered_count', 'expectation_count', thresholds['required_fact_coverage_min']),
        'official_fact_citation_coverage': ('cited_official_fact_count', 'official_fact_count', thresholds['official_fact_citation_coverage_min']),
        'citation_correctness': ('supported_relation_count', 'relation_count', thresholds['citation_correctness_min']),
        'unsupported_claim_rate': ('unsupported_count', 'audited_claim_count', thresholds['unsupported_claim_rate_max']),
        'clarify_answer_refuse_accuracy': ('passed_case_count', 'case_count', thresholds['clarify_answer_refuse_accuracy_min']),
        'trust_indicator_correctness': ('passed_case_count', 'case_count', thresholds['trust_indicator_correctness_min']),
        'fresh_tomato_min_material_source_rule_pass_rate': ('passed_case_count', 'applicable_case_count', thresholds['fresh_tomato_min_material_source_rule_pass_rate_min']),
        'privacy_requirement_compliance': ('compliant_count', 'expectation_count', 1.0),
        'evaluation_surface_completion': ('completed_case_count', 'case_count', 1.0),
    }
    for name, (numerator, denominator, threshold) in expected_metrics.items():
        metric = metrics[name]
        top, bottom = _count(metric[numerator]), _count(metric[denominator])
        require(bottom > 0 and top <= bottom, name + ' counts invalid')
        observed = top / bottom
        require(type(metric['observed']) in (int, float) and math.isclose(metric['observed'], observed, rel_tol=0, abs_tol=1e-12), name + ' arithmetic differs')
        require(metric['status'] == 'passed' and metric['threshold'] == threshold, name + ' threshold/status differs')
        require(observed <= threshold if name == 'unsupported_claim_rate' else observed >= threshold, name + ' threshold failed')
        for key in ('not_evaluable_count', 'not_evaluable_case_count', 'not_evaluable_relation_count'):
            if key in metric:
                require(_count(metric[key]) == 0, name + ' unevaluated items')
    for name, threshold in {'case_execution_errors': 0, 'forbidden_claims': thresholds['forbidden_claims_max'],
                            'forbidden_source_domain_violations': 0,
                            'personal_eligibility_conclusions': thresholds['personal_eligibility_conclusions_max']}.items():
        metric = metrics[name]
        require(_count(metric['observed']) <= threshold and metric['threshold'] == threshold and metric['status'] == 'passed', name + ' failed')
    domain = metrics['required_source_domain_coverage']
    domain_count = _count(domain['required_domain_count'])
    require(domain_count > 0 and _count(domain['missing_domain_count']) == 0 and domain['observed'] == domain['threshold'] == 1.0 and domain['status'] == 'passed', 'source domain coverage failed')
    require(set(metrics) == set(expected_metrics) | {
        'case_execution_errors', 'forbidden_claims', 'forbidden_source_domain_violations',
        'personal_eligibility_conclusions', 'required_source_domain_coverage',
    }, 'metric inventory differs')
    semantic_metrics = {'required_fact_coverage', 'forbidden_claims',
                        'privacy_requirement_compliance', 'citation_correctness',
                        'unsupported_claim_rate'}
    for name in set(metrics) - semantic_metrics:
        require(metrics[name] == origin['metrics'][name], name + ' machine result changed in review')
    require(_count(metrics['unsupported_claim_rate']['not_evaluable_count']) == 0,
            'unsupported claims remain unevaluated')
    require(metrics['unsupported_claim_rate']['audited_claim_count'] ==
            metrics['official_fact_citation_coverage']['official_fact_count'],
            'claim audit population differs')
    require(reviewed['contains_prompt_or_answer_text'] is False,
            'public report contains private text')
    # Denominators bind to the public original; semantic review may change supported counts.
    for name, (_, denominator, _) in expected_metrics.items():
        require(metrics[name][denominator] == origin['metrics'][name][denominator], name + ' denominator changed from origin')
    for field, name in (('required_facts', 'required_fact_coverage'), ('forbidden_claims', 'forbidden_claims')):
        expected = sum(len(case['final_answer_expectations'].get(field, [])) for case in dataset['cases'])
        require(metrics[name]['expectation_count'] == expected, name + ' dataset expectation count differs')
    for name in ('clarify_answer_refuse_accuracy', 'trust_indicator_correctness', 'fresh_tomato_min_material_source_rule_pass_rate'):
        metric = metrics[name]
        total = metric.get('case_count', metric.get('applicable_case_count'))
        require(_count(metric['passed_case_count']) + _count(metric['failed_case_count']) + _count(metric['not_evaluable_case_count']) == total, name + ' count partition differs')
    citation = metrics['citation_correctness']
    require(_count(citation['supported_relation_count']) + _count(citation['incorrect_relation_count']) + _count(citation['not_evaluable_relation_count']) == citation['relation_count'], 'citation partition differs')
    privacy = metrics['privacy_requirement_compliance']
    require(_count(privacy['compliant_count']) + _count(privacy['failed_count']) + _count(privacy['not_evaluable_count']) == privacy['expectation_count'], 'privacy partition differs')
    return failures
