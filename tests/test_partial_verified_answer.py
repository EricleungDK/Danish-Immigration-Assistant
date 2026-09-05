from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

from danish_rag.answer_pipeline import AnswerService, AnswerValidationError, LocalProviderAnswerGenerator
from danish_rag.answer_verification import VerificationError, partial_verified_answer
from tests.test_issue_13_evidence_safety import FixtureRetriever, evidence_fixture, provider_configuration
from tests.test_answer_policy_provenance import CLAIM, QUESTION


def positive(quote='e1s1'):
    return {'verdict': 'supported', 'failure_reason': 'none', 'witnesses': [{'quote_id': quote}]}


def negative():
    return {'verdict': 'unsupported', 'failure_reason': 'unsupported_detail', 'witnesses': []}


class ScriptedVerifier(LocalProviderAnswerGenerator):
    def __init__(self, payload, response):
        super().__init__()
        self.payload = deepcopy(payload)
        self.response = deepcopy(response)
        self.repairs = 0
        self.verifications = 0

    def generate(self, **kwargs):
        return deepcopy(self.payload)

    def repair_answer(self, **kwargs):
        self.repairs += 1
        return deepcopy(self.payload)

    def _generate_ollama_messages(self, **kwargs):
        self.verifications += 1
        return deepcopy(self.response)


class PartialVerifiedAnswerTests(unittest.TestCase):
    def fixture(self):
        evidence = [evidence_fixture('good', content=CLAIM),
                    evidence_fixture('negative', content='Registration is not available.')]
        sections = [
            {'kind': 'official_fact', 'text': CLAIM, 'citation_ids': ['good']},
            {'kind': 'official_fact', 'text': 'Registration is available.', 'citation_ids': ['negative']},
            {'kind': 'official_fact', 'text': 'Every certificate is accepted.', 'citation_ids': ['good']},
        ]
        answer = {'summary': CLAIM, 'sections': sections}
        response = {'checks': {'item_0': positive(), 'item_1': positive(),
                               'item_2': positive('e2s1'), 'item_3': negative()}}
        return answer, evidence, response

    def run_service(self, answer, evidence, response):
        generator = ScriptedVerifier(answer, response)
        result = AnswerService(retriever=FixtureRetriever(evidence), generator=generator).answer(
            QUESTION, replace(provider_configuration(), provider_id='ollama'))
        return result.answer, generator

    def test_exhausted_repair_retains_only_fully_verified_facts_and_all_app_boundaries(self):
        answer, evidence, response = self.fixture()
        evidence[0]['source_health'] = 'overdue-policy-usable'
        evidence[1]['agreement_state'] = 'conflict'
        evidence.append(evidence_fixture('blocked', content='Unreviewed content.', review_state='changed-unreviewed'))
        result, generator = self.run_service(answer, evidence, response)
        self.assertEqual(generator.repairs, 1)
        self.assertEqual(generator.verifications, 2)
        facts = [s for s in result['sections'] if s['kind'] == 'official_fact']
        self.assertEqual([s['text'] for s in facts], [CLAIM])
        self.assertEqual(result['summary'], CLAIM)
        self.assertEqual(result['verification'], {
            'status': 'partial', 'retained_official_fact_count': 1, 'omitted_section_count': 2,
            'omitted_reasons': {'unsupported': 1, 'hard_constraint': 1, 'dependent_statement': 0, 'non_fact': 0}})
        self.assertTrue(any('personal eligibility' in s['text'] for s in result['sections']))
        self.assertTrue(any('overdue' in s['text'] for s in result['sections']))
        self.assertTrue(any('conflict' in s['text'] for s in result['sections']))
        self.assertTrue(any('blocked by source policy' in s['text'] for s in result['sections']))
        self.assertTrue(any('answer is partial' in s['text'] for s in result['sections']))

    def test_all_unsupported_or_only_dependent_claims_fail_closed(self):
        for dependent in (False, True):
            answer, evidence, response = self.fixture()
            if dependent:
                answer['sections'][0]['text'] = 'This also meets the requirement.'
                answer['summary'] = answer['sections'][0]['text']
            else:
                response['checks']['item_1'] = negative()
            with self.subTest(dependent=dependent), self.assertRaises(AnswerValidationError):
                self.run_service(answer, evidence, response)

    def test_missing_malformed_and_unknown_witness_responses_never_produce_partial_answer(self):
        for defect in ('missing', 'wrong_type', 'unknown', 'invalid_reason', 'extra'):
            answer, evidence, response = self.fixture()
            if defect == 'missing': response['checks'].pop('item_3')
            if defect == 'wrong_type': response['checks']['item_3']['witnesses'] = None
            if defect == 'unknown': response['checks']['item_2'] = positive('invented')
            if defect == 'invalid_reason': response['checks']['item_3']['failure_reason'] = []
            if defect == 'extra': response['checks']['item_3']['trusted'] = True
            with self.subTest(defect=defect), self.assertRaises(AnswerValidationError):
                self.run_service(answer, evidence, response)

    def test_dangling_reference_is_not_retained_even_with_positive_semantic_verdict(self):
        answer, evidence, response = self.fixture()
        answer['sections'][2]['text'] = 'It meets all conditions described above.'
        response['checks']['item_3'] = positive()
        partial, bindings, status = partial_verified_answer(answer, evidence, response)
        self.assertEqual(len(partial['sections']), 1)
        self.assertEqual(status['omitted_reasons']['dependent_statement'], 1)
        self.assertTrue(bindings)

    def test_complete_answer_is_unchanged_and_never_enters_partial_path(self):
        answer, evidence, response = self.fixture()
        answer['sections'] = answer['sections'][:1]
        response['checks'] = {key: response['checks'][key] for key in ('item_0', 'item_1')}
        with patch('danish_rag.answer_pipeline.partial_verified_answer', side_effect=AssertionError('not needed')):
            result, generator = self.run_service(answer, evidence, response)
        self.assertEqual(generator.repairs, 0)
        self.assertNotIn('verification', result)
        self.assertFalse(any('answer is partial' in s['text'] for s in result['sections']))

    def test_negative_summary_verdict_cannot_be_overruled_by_duplicate_positive_fact(self):
        answer, evidence, response = self.fixture()
        response['checks']['item_0'] = negative()
        with self.assertRaises(AnswerValidationError):
            self.run_service(answer, evidence, response)

    def test_partial_validator_does_not_mislabel_an_answer_with_no_omissions(self):
        answer, evidence, response = self.fixture()
        answer['sections'] = answer['sections'][:1]
        response['checks'] = {key: response['checks'][key] for key in ('item_0', 'item_1')}
        with self.assertRaisesRegex(VerificationError, 'actual omissions'):
            partial_verified_answer(answer, evidence, response)

    def test_resource_error_cannot_use_an_earlier_partial_response(self):
        answer, evidence, response = self.fixture()
        generator = ScriptedVerifier(answer, response)
        original = generator._generate_ollama_messages
        def resource_failure(**kwargs):
            if generator.repairs:
                raise AnswerValidationError('Verification protocol exceeds resource bound.')
            return original(**kwargs)
        generator._generate_ollama_messages = resource_failure
        with self.assertRaisesRegex(AnswerValidationError, 'resource bound'):
            AnswerService(retriever=FixtureRetriever(evidence), generator=generator).answer(
                QUESTION, replace(provider_configuration(), provider_id='ollama'))
