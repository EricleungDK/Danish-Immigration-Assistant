from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from danish_rag.answer_pipeline import (
    AnswerService, AnswerValidationError, LocalProviderAnswerGenerator,
)
from danish_rag.answer_verification import claim_binding
from tests.test_issue_13_evidence_safety import (
    FixtureRetriever, evidence_fixture, provider_configuration,
)


CLAIM = 'Prøve i Dansk 2 is the basic Danish language requirement for permanent residence.'
QUESTION = ('I passed PD2, have lived in Denmark for 7 years, and have a job. '
            'Do I qualify for permanent residence?')


class SourceOnlyVerifier(LocalProviderAnswerGenerator):
    """Reject non-source prose so the public seam must establish its origin."""

    def __init__(self, *, extra_section=None, malformed_first=False):
        super().__init__()
        self.extra_section = extra_section
        self.malformed_first = malformed_first
        self.verified_payloads = []
        self.repairs = 0

    def _answer(self):
        sections = [{'kind': 'official_fact', 'text': CLAIM, 'citation_ids': ['source']}]
        if self.extra_section is not None:
            sections.append(deepcopy(self.extra_section))
        return {'summary': 'An introduction.', 'sections': sections}

    def generate(self, **kwargs):
        if self.malformed_first:
            return {'summary': 'Malformed.', 'sections': [None]}
        return self._answer()

    def repair_answer(self, **kwargs):
        self.repairs += 1
        return self._answer()

    def verify_answer(self, payload, *, evidence, configuration):
        self.verified_payloads.append(deepcopy(payload))
        sections = payload.get('sections', [])
        if any(not isinstance(section, dict) for section in sections):
            raise AnswerValidationError('Malformed model sections.')
        if any(section['kind'] != 'official_fact' for section in sections):
            raise AnswerValidationError('No external source supports this prose.')
        return frozenset([
            claim_binding(payload['summary'], ['source'], evidence, kind='summary'),
            *[claim_binding(s['text'], s['citation_ids'], evidence,
                            kind=s['kind'], position=i)
              for i, s in enumerate(sections, 1)],
        ])


class AnswerPolicyProvenanceTests(unittest.TestCase):
    def answer(self, generator, *, overdue=False):
        source = evidence_fixture('source', content=CLAIM)
        if overdue:
            source['source_health'] = 'overdue-policy-usable'
        return AnswerService(retriever=FixtureRetriever([source]), generator=generator).answer(
            QUESTION, replace(provider_configuration(), provider_id='ollama'))

    def test_program_refusal_and_metadata_warning_are_added_after_source_verification(self):
        generator = SourceOnlyVerifier()
        result = self.answer(generator, overdue=True)
        self.assertEqual(generator.repairs, 0)
        self.assertEqual(len(generator.verified_payloads[0]['sections']), 1)
        self.assertEqual(result.answer['summary'], CLAIM)
        kinds = [section['kind'] for section in result.answer['sections']]
        self.assertEqual(kinds, ['official_fact', 'refusal', 'source_warning'])
        self.assertIn('personal eligibility', result.answer['sections'][1]['text'])
        self.assertIn('overdue', result.answer['sections'][2]['text'])

    def test_model_section_cannot_claim_program_origin_to_bypass_verifier(self):
        generator = SourceOnlyVerifier(extra_section={
            'kind': 'refusal', 'text': 'Your certificate is never accepted.',
            'citation_ids': [], 'origin': 'application', 'verified': True,
        })
        with self.assertRaises(AnswerValidationError):
            self.answer(generator)
        self.assertEqual(generator.repairs, 1)
        self.assertTrue(all(len(p['sections']) == 2 for p in generator.verified_payloads))

    def test_malformed_model_sections_repair_before_program_augmentation(self):
        generator = SourceOnlyVerifier(malformed_first=True)
        result = self.answer(generator)
        self.assertEqual(generator.repairs, 1)
        self.assertEqual(result.answer['summary'], CLAIM)


class ProviderRepairResourceTests(unittest.TestCase):
    def test_generation_groups_only_matching_source_versions_without_adding_citations(self):
        from tests.test_issue_9_answer_path import RecordingOllamaGenerator
        from danish_rag.answer_pipeline import answer_schema
        provenance = {
            'source_id': 'official-page', 'source_document_id': 'reviewed-page',
            'source_content_sha256': 'a' * 64,
            'normalized_document_sha256': 'b' * 64,
            'normalized_extraction_sha256': 'c' * 64,
            'official_url': 'https://www.nyidanmark.dk/example',
            'final_url': 'https://www.nyidanmark.dk/example',
            'language': 'en-GB', 'corpus_identity': 'kr-test',
            'knowledge_release_id': 'kr-test',
        }
        sources = [{**evidence_fixture(key, content=CLAIM), **provenance}
                   for key in ['a', 'b', 'c']]
        sources[2]['normalized_document_sha256'] = 'd' * 64
        proposed = {'summary': CLAIM, 'sections': [
            {'kind': 'official_fact', 'text': CLAIM, 'citation_ids': ['a']}]}
        wire_proposed = {**proposed, 'sections': [{**s, 'citation_ids': ['e1']} for s in proposed['sections']]}
        generator = RecordingOllamaGenerator([json.dumps(wire_proposed)])
        result = generator.generate(
            question=QUESTION, normalized_question=QUESTION, evidence=sources,
            configuration=replace(provider_configuration(), provider_id='ollama'),
            schema=answer_schema(['a', 'b', 'c']),
        )
        sent = json.loads(generator.payloads[0]['messages'][1]['content'])['approved_official_evidence']
        self.assertEqual(sent[0]['source_document_group'], sent[1]['source_document_group'])
        self.assertNotEqual(sent[0]['source_document_group'], sent[2]['source_document_group'])
        self.assertEqual([item['citation_id'] for item in sent], ['e1', 'e2', 'e3'])
        self.assertEqual(result['sections'][0]['citation_ids'], ['a'])
        system = generator.payloads[0]['messages'][0]['content']
        self.assertIn('Application response boundary:', system)
        self.assertIn('cannot decide personal eligibility', system)
        self.assertIn('Explain the supported general rule', system)

    def test_repair_preserves_prior_answer_and_evidence_with_expanded_context(self):
        from tests.test_issue_9_answer_path import RecordingOllamaGenerator
        from danish_rag.answer_pipeline import answer_schema
        source = evidence_fixture('source', content=CLAIM)
        previous = {'summary': CLAIM, 'sections': [
            {'kind': 'official_fact', 'text': CLAIM, 'citation_ids': ['source']}]}
        wire_previous = {**previous, 'sections': [{**s, 'citation_ids': ['e1']} for s in previous['sections']]}
        generator = RecordingOllamaGenerator([json.dumps(wire_previous)])
        generator.repair_answer(
            question=QUESTION, normalized_question=QUESTION,
            evidence=[source], configuration=replace(provider_configuration(), provider_id='ollama'),
            schema=answer_schema(['source']), rejection='Missing qualification.',
            previous_answer=previous,
        )
        request = generator.payloads[0]
        self.assertEqual(request['options']['num_ctx'], 8192)
        self.assertTrue(any(message['role'] == 'assistant' and
                            json.loads(message['content']) == wire_previous
                            for message in request['messages']))
        original_input = json.loads(request['messages'][1]['content'])
        self.assertEqual(original_input['approved_official_evidence'][0]['content'], CLAIM)

    def test_oversized_verifier_correction_fails_before_another_provider_call(self):
        from tests.test_issue_9_answer_path import RecordingOllamaGenerator
        proposed = {'summary': CLAIM, 'sections': [
            {'kind': 'official_fact', 'text': CLAIM, 'citation_ids': ['source']}]}
        generator = RecordingOllamaGenerator([json.dumps({'checks': {}, 'padding': 'x' * 24000})])
        with self.assertRaises(AnswerValidationError):
            generator.verify_answer(
                proposed, evidence=[evidence_fixture('source', content=CLAIM)],
                configuration=replace(provider_configuration(), provider_id='ollama'),
            )
        self.assertEqual(len(generator.payloads), 1)

    def test_inner_json_repair_cannot_exceed_verifier_message_bound(self):
        from tests.test_issue_9_answer_path import RecordingOllamaGenerator
        proposed = {'summary': CLAIM, 'sections': [
            {'kind': 'official_fact', 'text': CLAIM, 'citation_ids': ['source']}]}
        generator = RecordingOllamaGenerator(['invalid JSON'])
        with patch('danish_rag.answer_pipeline.verification_messages', return_value=[
            {'role': 'user', 'content': 'x' * 23999},
        ]), self.assertRaises(AnswerValidationError):
            generator.verify_answer(
                proposed, evidence=[evidence_fixture('source', content=CLAIM)],
                configuration=replace(provider_configuration(), provider_id='ollama'),
            )
        self.assertEqual(len(generator.payloads), 1)


if __name__ == '__main__':
    unittest.main()
