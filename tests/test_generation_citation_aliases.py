import json
import unittest
from copy import deepcopy
from dataclasses import replace

from danish_rag.answer_pipeline import AnswerValidationError, answer_schema
from tests.test_issue_13_evidence_safety import evidence_fixture, provider_configuration
from tests.test_issue_9_answer_path import RecordingOllamaGenerator


class GenerationCitationAliasTests(unittest.TestCase):
    def sources(self):
        return [evidence_fixture('official-page-with-shared-prefix-' + suffix,
                                 content='Registration is available.') for suffix in ('first', 'second')]

    def payload(self, citations):
        return {'summary': 'Registration is available.', 'sections': [
            {'kind': 'official_fact', 'text': 'Registration is available.', 'citation_ids': citations}]}

    def call(self, generator, sources):
        return generator.generate(question='Where do I register?', normalized_question='Where do I register?',
            evidence=sources, configuration=replace(provider_configuration(), provider_id='ollama'),
            schema=answer_schema([source['citation_id'] for source in sources]))

    def test_generation_short_aliases_restore_exact_canonical_ids_before_return(self):
        sources = self.sources()
        original = deepcopy(sources)
        generator = RecordingOllamaGenerator([json.dumps(self.payload(['e2']))])
        result = self.call(generator, sources)
        self.assertEqual(result['sections'][0]['citation_ids'], [sources[1]['citation_id']])
        request = generator.payloads[0]
        sent = json.loads(request['messages'][1]['content'])['approved_official_evidence']
        self.assertEqual([source['citation_id'] for source in sent], ['e1', 'e2'])
        enum = request['format']['properties']['sections']['items']['properties']['citation_ids']['items']['enum']
        self.assertEqual(enum, ['e1', 'e2'])
        self.assertEqual(sources, original)

    def test_unknown_canonical_duplicate_and_malformed_aliases_fail_closed(self):
        sources = self.sources()
        for citations in (['e3'], [sources[0]['citation_id']], ['e1', 'e1'], [1], [' e1']):
            generator = RecordingOllamaGenerator([json.dumps(self.payload(citations))])
            with self.subTest(citations=citations), self.assertRaises(AnswerValidationError):
                self.call(generator, sources)

    def test_duplicate_input_identity_rejects_before_any_provider_request(self):
        sources = self.sources()
        sources[1]['citation_id'] = sources[0]['citation_id']
        generator = RecordingOllamaGenerator([])
        with self.assertRaises(AnswerValidationError):
            self.call(generator, sources)
        self.assertEqual(generator.payloads, [])

    def test_repair_uses_same_alias_namespace_and_preserves_canonical_previous_answer(self):
        sources = self.sources()
        previous = self.payload([sources[1]['citation_id']])
        original = deepcopy(previous)
        generator = RecordingOllamaGenerator([json.dumps(self.payload(['e1']))])
        result = generator.repair_answer(question='Where do I register?', normalized_question='Where do I register?',
            evidence=sources, configuration=replace(provider_configuration(), provider_id='ollama'),
            schema=answer_schema([source['citation_id'] for source in sources]),
            rejection='Wrong citation.', previous_answer=previous)
        prior = next(json.loads(message['content']) for message in generator.payloads[0]['messages']
                     if message['role'] == 'assistant')
        self.assertEqual(prior['sections'][0]['citation_ids'], ['e2'])
        self.assertEqual(result['sections'][0]['citation_ids'], [sources[0]['citation_id']])
        self.assertEqual(previous, original)

    def test_schema_subset_is_not_expanded_by_alias_projection(self):
        sources = self.sources()
        schema = answer_schema([sources[0]['citation_id']])
        original = deepcopy(schema)
        generator = RecordingOllamaGenerator([json.dumps(self.payload(['e2']))])
        with self.assertRaises(AnswerValidationError):
            generator.generate(question='Where do I register?', normalized_question='Where do I register?',
                evidence=sources, configuration=replace(provider_configuration(), provider_id='ollama'), schema=schema)
        self.assertEqual(schema, original)
        enum = generator.payloads[0]['format']['properties']['sections']['items']['properties']['citation_ids']['items']['enum']
        self.assertEqual(enum, ['e1'])

    def test_openai_compatible_generation_uses_identical_alias_boundary(self):
        from tests.test_issue_9_answer_path import RecordingOpenAICompatibleGenerator
        sources = self.sources()
        generator = RecordingOpenAICompatibleGenerator(self.payload(['e2']))
        result = generator.generate(question='Where do I register?', normalized_question='Where do I register?',
            evidence=sources, configuration=replace(provider_configuration(), provider_id='openai_compatible'),
            schema=answer_schema([source['citation_id'] for source in sources]))
        self.assertEqual(result['sections'][0]['citation_ids'], [sources[1]['citation_id']])
        schema = generator.payloads[0]['response_format']['json_schema']['schema']
        self.assertEqual(schema['properties']['sections']['items']['properties']['citation_ids']['items']['enum'], ['e1', 'e2'])


class GenerationCitationPackageTests(unittest.TestCase):
    payload = GenerationCitationAliasTests.payload
    call = GenerationCitationAliasTests.call

    def sources(self):
        sources = GenerationCitationAliasTests().sources()
        provenance = {'source_id': 'shared-page', 'source_document_id': 'shared-document',
            'source_content_sha256': 'a' * 64, 'normalized_document_sha256': 'b' * 64,
            'normalized_extraction_sha256': 'c' * 64, 'official_url': 'https://example.gov/page',
            'final_url': 'https://example.gov/page', 'language': 'en',
            'corpus_identity': 'approved-corpus', 'knowledge_release_id': 'approved-release'}
        return [{**source, **provenance} for source in sources]

    def test_explicit_package_expands_exact_declared_members_and_individual_stays_single(self):
        sources = self.sources()
        for selected, expected in ((['g1'], [source['citation_id'] for source in sources]),
                                   (['e2'], [sources[1]['citation_id']])):
            generator = RecordingOllamaGenerator([json.dumps(self.payload(selected))])
            result = self.call(generator, sources)
            self.assertEqual(result['sections'][0]['citation_ids'], expected)
            sent = json.loads(generator.payloads[0]['messages'][1]['content'])
            self.assertEqual(sent['explicit_citation_packages'], {'g1': ['e1', 'e2']})

    def test_package_overlap_duplicates_and_unknown_selection_fail_closed(self):
        for selected in (['g1', 'e1'], ['e2', 'g1'], ['g1', 'g1'], ['g99'], ['g1', {}]):
            generator = RecordingOllamaGenerator([json.dumps(self.payload(selected))])
            with self.subTest(selected=selected), self.assertRaises(AnswerValidationError):
                self.call(generator, self.sources())

    def test_mismatch_missing_provenance_and_schema_subset_never_form_a_package(self):
        for mode in ('mismatch', 'missing', 'subset'):
            sources = self.sources()
            allowed = [source['citation_id'] for source in sources]
            if mode == 'mismatch':
                sources[1]['normalized_document_sha256'] = 'd' * 64
            elif mode == 'missing':
                sources[1].pop('language')
            else:
                allowed = allowed[:1]
            generator = RecordingOllamaGenerator([json.dumps(self.payload(['g1']))])
            with self.subTest(mode=mode), self.assertRaises(AnswerValidationError):
                generator.generate(question='Where do I register?', normalized_question='Where do I register?',
                    evidence=sources, configuration=replace(provider_configuration(), provider_id='ollama'),
                    schema=answer_schema(allowed))
            sent = json.loads(generator.payloads[0]['messages'][1]['content'])
            self.assertEqual(sent['explicit_citation_packages'], {})

    def test_repair_projects_canonical_members_individually_without_inventing_package_choice(self):
        sources = self.sources()
        previous = self.payload([source['citation_id'] for source in sources])
        original = deepcopy(previous)
        generator = RecordingOllamaGenerator([json.dumps(self.payload(['g1']))])
        result = generator.repair_answer(question='Where do I register?', normalized_question='Where do I register?',
            evidence=sources, configuration=replace(provider_configuration(), provider_id='ollama'),
            schema=answer_schema([source['citation_id'] for source in sources]),
            rejection='Missing context.', previous_answer=previous)
        sent_previous = next(json.loads(message['content']) for message in generator.payloads[0]['messages']
                             if message['role'] == 'assistant')
        self.assertEqual(sent_previous['sections'][0]['citation_ids'], ['e1', 'e2'])
        self.assertEqual(result['sections'][0]['citation_ids'], previous['sections'][0]['citation_ids'])
        self.assertEqual(previous, original)

    def test_package_selection_does_not_bypass_each_member_witness_requirement(self):
        from danish_rag.answer_verification import VerificationError, validate_verification
        sources = self.sources()
        generator = RecordingOllamaGenerator([json.dumps(self.payload(['g1']))])
        answer = self.call(generator, sources)
        checks = {'item_0': {'verdict': 'supported', 'failure_reason': 'none',
                            'witnesses': [{'quote_id': 'e1s1'}]},
                  'item_1': {'verdict': 'supported', 'failure_reason': 'none',
                            'witnesses': [{'quote_id': 'e1s1'}]}}
        with self.assertRaisesRegex(VerificationError, 'omitted a cited source witness'):
            validate_verification(answer, sources, {'checks': checks})


class CitationRepairDiagnosticTests(unittest.TestCase):
    sources = GenerationCitationPackageTests.sources
    payload = GenerationCitationAliasTests.payload
    def diagnostic(self, sources, citations, text, allowed=None):
        previous = self.payload(citations)
        previous['sections'][0]['text'] = text
        reply_alias = 'e' + str(next(i for i, source in enumerate(sources, 1) if source['citation_id'] in citations))
        generator = RecordingOllamaGenerator([json.dumps(self.payload([reply_alias]))])
        generator.repair_answer(question='Explain the general rule.', normalized_question='Explain the general rule.',
            evidence=sources, configuration=replace(provider_configuration(), provider_id='ollama'),
            schema=answer_schema(allowed or [s['citation_id'] for s in sources]),
            rejection='Unsupported proposition.', previous_answer=previous)
        return json.loads(generator.payloads[0]['messages'][-1]['content'])['citation_anchor_diagnostics']

    def test_missing_anchor_identifies_explicit_package_without_auto_selection(self):
        sources = self.sources()
        sources[0]['content'] = 'The certificates meet Danish language test 3.'
        sources[1]['content'] = 'A passed certificate requires an average of 6.'
        result = self.diagnostic(sources, [sources[1]['citation_id']], 'The certificate meets Danish language test 3 with an average of 6.')
        self.assertEqual(result['items'][0]['item_index'], 1)
        self.assertEqual(result['items'][0]['missing_exam_anchors'], ['prøve i dansk 3'])
        self.assertEqual(result['items'][0]['context_package_options'], ['g1'])

    def test_complete_current_context_and_semantic_only_failure_have_no_diagnostic(self):
        sources = self.sources()
        sources[0]['content'] = 'Danish language test 3 requires 6.'
        for citations, text in (([s['citation_id'] for s in sources], 'Danish language test 3 requires 6.'),
                                ([sources[0]['citation_id']], 'This certificate is always accepted.')):
            self.assertEqual(self.diagnostic(sources, citations, text)['items'], [])

    def test_unrelated_disallowed_and_incomplete_coverage_never_suggest_package(self):
        for mode in ('unrelated', 'disallowed', 'incomplete', 'missing'):
            sources = self.sources();sources[0]['content'] = 'Danish language test 3 requires 6.'
            sources[1]['content'] = 'A passed certificate is required.'
            allowed = None
            if mode == 'unrelated': sources[0]['normalized_document_sha256'] = 'd' * 64
            if mode == 'missing': sources[0].pop('language')
            if mode == 'disallowed': allowed = [sources[1]['citation_id']]
            text = 'Danish language test 3 requires ' + ('99.' if mode == 'incomplete' else '6.')
            result = self.diagnostic(sources, [sources[1]['citation_id']], text, allowed)
            self.assertTrue(result['items'])
            self.assertEqual(result['items'][0]['context_package_options'], [])

    def test_numeric_diagnostic_uses_existing_locale_guard(self):
        sources = self.sources()
        sources[0]['language'] = sources[1]['language'] = 'da'
        sources[0]['content'] = 'Prisen er 1.000,50 kroner.'
        self.assertEqual(self.diagnostic(sources, [sources[0]['citation_id']], 'The price is 1,000.50.')['items'], [])
        result = self.diagnostic(sources, [sources[0]['citation_id']], 'The price is 1.00.')
        self.assertEqual(result['items'][0]['missing_number_anchors'], ['1.00'])
