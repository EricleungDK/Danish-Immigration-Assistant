"""Evidence-bounded answer module through the answer service and the fake transport (#75).

Generation, verification, and repair responses are scripted in order on `FakeTransport`;
the service is built with an explicit verifier. Nothing subclasses a production generator.
"""

import json
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from danish_rag.answer_pipeline import (
    AnswerPipelineError,
    AnswerService,
    AnswerValidationError,
    ModelAnswerVerifier,
    TransportAnswerGenerator,
    fixed_transport,
)
from danish_rag.local_model_transport import (
    FakeTransport,
    ModelMissingError,
    ModelProtocolError,
    ModelTimeoutError,
    ModelUnreachableError,
    OllamaTransport,
    OpenAICompatibleTransport,
)
from tests.answer_transport import (
    lexical_service, model_service, requests, script, wire,
)
from tests.evaluation_time import fixed_clock
from tests.test_issue_13_evidence_safety import (
    FixtureRetriever, evidence_fixture, provider_configuration,
)

CLAIM = 'Register directly at the language centre.'
QUESTION = 'Where do I register for Prøve i Dansk 2?'
ALIASES = {'source': 'e1'}


def configuration():
    return replace(provider_configuration(), provider_id='ollama',
                   endpoint='http://127.0.0.1:11434')


def supported(quote='e1s1'):
    return {'verdict': 'supported', 'failure_reason': 'none', 'witnesses': [{'quote_id': quote}]}


def unsupported():
    return {'verdict': 'unsupported', 'failure_reason': 'unsupported_detail', 'witnesses': []}


def checks(*items):
    return {'checks': {f'item_{index}': item for index, item in enumerate(items)}}


def draft(text=CLAIM, citations=('source',), summary='An introduction.'):
    return {'summary': summary, 'sections': [
        {'kind': 'official_fact', 'text': text, 'citation_ids': list(citations)}]}


def ask(service, question=QUESTION):
    return service.answer(question, configuration())


class ModelRegimeTests(unittest.TestCase):
    def retriever(self):
        return FixtureRetriever([evidence_fixture('source', content=CLAIM)])

    def test_generation_then_separate_verification_request_produces_the_answer(self):
        transport = script(FakeTransport(), wire(draft(), ALIASES), checks(supported(), supported()))
        result = ask(model_service(transport, self.retriever()))
        self.assertEqual(result.answer['summary'], CLAIM)
        self.assertEqual(result.answer['sections'][0]['citation_ids'], ['source'])
        self.assertEqual(result.answer['sections'][0]['text'], CLAIM)
        generation, verification = requests(transport)
        self.assertIsNone(generation['context_tokens'])
        self.assertEqual(verification['context_tokens'], 8192)
        self.assertIn('checks', verification['schema']['properties'])
        self.assertIn('separate conservative evidence verifier', verification['messages'][0]['content'])
        self.assertEqual(transport.remaining, 0)

    def test_rejected_verification_triggers_one_repair_then_verifies_the_repair(self):
        transport = script(
            FakeTransport(), wire(draft(), ALIASES),
            checks(unsupported(), unsupported()),
            wire(draft(), ALIASES), checks(supported(), supported()))
        result = ask(model_service(transport, self.retriever()))
        self.assertEqual(result.answer['sections'][0]['text'], CLAIM)
        sent = requests(transport)
        self.assertEqual(len(sent), 4)
        repair = sent[2]
        self.assertEqual(repair['context_tokens'], 8192)
        self.assertTrue(any(
            message['role'] == 'assistant'
            and json.loads(message['content'])['sections'][0]['citation_ids'] == ['e1']
            for message in repair['messages']))
        self.assertIn('failed the separate evidence check', repair['messages'][-1]['content'])
        self.assertEqual(sent[3]['context_tokens'], 8192)
        self.assertEqual(transport.remaining, 0)

    def test_repair_is_attempted_once_and_exhausted_repair_fails_closed(self):
        reject = checks(unsupported(), unsupported())
        transport = script(FakeTransport(), wire(draft(), ALIASES), reject,
                           wire(draft(), ALIASES), reject)
        with self.assertRaises(AnswerValidationError):
            ask(model_service(transport, self.retriever()))
        self.assertEqual(len(requests(transport)), 4)

    def test_wrong_witness_is_corrected_in_the_verifier_without_a_writer_repair(self):
        content = 'Prøve i Dansk 2 is the basic requirement. Prøve i Dansk 3 is supplementary.'
        claim = 'Prøve i Dansk 2 is the basic requirement.'
        retriever = FixtureRetriever([evidence_fixture('source', content=content)])
        transport = script(
            FakeTransport(), wire(draft(claim), ALIASES),
            checks(supported('e1s2'), supported('e1s2')),
            checks(supported('e1s1'), supported('e1s1')))
        result = ask(model_service(transport, retriever))
        self.assertEqual(result.answer['sections'][0]['text'], claim)
        sent = requests(transport)
        self.assertEqual(len(sent), 3)
        self.assertIn('unsupported exam identities', sent[2]['messages'][-1]['content'])
        self.assertEqual(sent[2]['context_tokens'], 8192)

    def test_negative_semantic_verdict_gets_no_verifier_correction(self):
        reject = checks(unsupported(), unsupported())
        transport = script(FakeTransport(), wire(draft(), ALIASES), reject,
                           wire(draft(), ALIASES), reject)
        with self.assertRaises(AnswerValidationError):
            ask(model_service(transport, self.retriever()))
        sent = requests(transport)
        self.assertFalse(any('failed an application constraint' in message['content']
                             for request in sent for message in request['messages']))

    def test_truncated_structured_output_fails_closed_with_an_actionable_error(self):
        transport = FakeTransport()
        for _ in range(2):
            transport.queue_chat(json.dumps(wire(draft(), ALIASES)))
            transport.queue_chat('{', done_reason='length')
        with self.assertRaisesRegex(AnswerValidationError, 'exhausted the available context'):
            ask(model_service(transport, self.retriever()))
        self.assertEqual(len(requests(transport)), 4)

    def test_ambiguous_structured_output_is_retried_once_then_rejected(self):
        valid = json.dumps(wire(draft(), ALIASES))
        transport = script(FakeTransport(), valid + '\n' + valid, valid,
                           checks(supported(), supported()))
        result = ask(model_service(transport, self.retriever()))
        self.assertEqual(result.answer['sections'][0]['text'], CLAIM)
        retry = requests(transport)[1]
        self.assertIn('invalid', retry['messages'][-1]['content'])
        self.assertIsNone(retry['context_tokens'])
        broken = script(FakeTransport(), valid + '\n' + valid, valid + '\n' + valid)
        with self.assertRaises(AnswerValidationError):
            ask(model_service(broken, self.retriever()))
        self.assertEqual(len(requests(broken)), 2)

    def test_reply_without_message_content_is_reasked_once_then_fails_validation(self):
        replies = iter([{'message': {}}, {'message': {'content': json.dumps(wire(draft(), ALIASES))}},
                        {'message': {'content': json.dumps(checks(supported(), supported()))}}])

        def fake_request(self, method, path, payload=None, *, missing_statuses=()):
            return next(replies)

        with patch.object(OllamaTransport, '_request', fake_request):
            transport = OllamaTransport(configuration().endpoint, timeout_seconds=1)
            result = ask(model_service(transport, self.retriever()))
        self.assertEqual(result.answer['sections'][0]['text'], CLAIM)

        broken = iter([{}, {}])
        with patch.object(OllamaTransport, '_request', lambda *a, **k: next(broken)):
            transport = OllamaTransport(configuration().endpoint, timeout_seconds=1)
            with self.assertRaises(AnswerValidationError):
                ask(model_service(transport, self.retriever()))

    def test_provider_failure_copy_names_the_actionable_problem(self):
        for error, expected in (
            (ModelMissingError('gone'), 'fixture-model'),
            (ModelProtocolError('HTTP 500'), 'supports structured output'),
        ):
            transport = FakeTransport()
            transport.queue_error(error)
            with self.subTest(error=type(error).__name__):
                with self.assertRaisesRegex(AnswerPipelineError, expected):
                    ask(model_service(transport, self.retriever()))

    def verify_directly(self, transport):
        verifier = ModelAnswerVerifier(fixed_transport(transport))
        return verifier.verified_claims(
            draft(summary=CLAIM), evidence=[evidence_fixture('source', content=CLAIM)],
            configuration=configuration())

    def test_verification_retry_keeps_the_message_bound(self):
        transport = script(FakeTransport(), 'invalid JSON')
        with patch('danish_rag.answer_pipeline.verification_messages',
                   return_value=[{'role': 'user', 'content': 'x' * 23999}]):
            with self.assertRaises(AnswerValidationError):
                self.verify_directly(transport)
        self.assertEqual(len(requests(transport)), 1)

    def test_oversized_verifier_correction_fails_before_another_request(self):
        transport = script(FakeTransport(), {'checks': {}, 'padding': 'x' * 24000})
        with self.assertRaises(AnswerValidationError):
            self.verify_directly(transport)
        self.assertEqual(len(requests(transport)), 1)

    def test_program_authored_refusal_needs_no_model_request(self):
        transport = FakeTransport()
        result = ask(model_service(transport, self.retriever()),
                     'Can you give me legal advice on how to argue my permanent residence language case?')
        self.assertEqual(result.answer['response_kind'], 'refusal')
        self.assertIn('cannot provide legal advice', result.answer['summary'])
        self.assertEqual(requests(transport), [])

    def test_model_cannot_claim_program_origin_to_bypass_verification(self):
        spoofed = draft()
        spoofed['sections'].append({
            'kind': 'refusal', 'text': 'Your certificate is never accepted.',
            'citation_ids': [], 'origin': 'application', 'verified': True})
        verdicts = checks(supported(), supported(), unsupported())
        transport = script(FakeTransport(), wire(spoofed, ALIASES), verdicts,
                           wire(spoofed, ALIASES), verdicts)
        result = ask(model_service(transport, self.retriever()))
        # The fully verified fact survives; the spoofed prose is omitted as partial.
        self.assertEqual([s['text'] for s in result.answer['sections'][:1]], [CLAIM])
        self.assertFalse(any('never accepted' in s['text'] for s in result.answer['sections']))
        self.assertEqual(result.answer['verification']['status'], 'partial')

    def test_transport_failures_become_answer_pipeline_errors_with_actionable_text(self):
        for error, expected in (
            (ModelUnreachableError('down'), 'Local generation provider is unavailable'),
            (ModelTimeoutError('slow'), 'timed out while preparing the answer'),
        ):
            transport = FakeTransport()
            transport.queue_error(error)
            with self.subTest(error=type(error).__name__):
                with self.assertRaisesRegex(AnswerPipelineError, expected):
                    ask(model_service(transport, self.retriever()))


class VerifierChoiceTests(unittest.TestCase):
    """The verifier is chosen when the service is built; the generator class never is."""

    def retriever(self):
        return FixtureRetriever([evidence_fixture('source', content=CLAIM)])

    def test_lexical_verifier_over_a_transport_generator_makes_no_verification_request(self):
        transport = script(FakeTransport(), wire(draft(summary=CLAIM), ALIASES))
        result = ask(lexical_service(transport, self.retriever()))
        self.assertEqual(result.answer['sections'][0]['text'], CLAIM)
        self.assertEqual(len(requests(transport)), 1)

    def test_lexical_verifier_never_repairs(self):
        transport = script(FakeTransport(), wire(draft('Registration costs 500 kr.'), ALIASES))
        with self.assertRaises(AnswerValidationError):
            ask(lexical_service(transport, self.retriever()))
        self.assertEqual(len(requests(transport)), 1)

    def test_model_verifier_applies_to_any_generator_with_the_repair_capability(self):
        class DuckGenerator:
            def generate(self, **kwargs):
                return draft()
            def repair_answer(self, **kwargs):
                raise AssertionError('not needed')

        transport = script(FakeTransport(), checks(supported(), supported()))
        service = AnswerService(
            retriever=self.retriever(), generator=DuckGenerator(),
            verifier=ModelAnswerVerifier(fixed_transport(transport)), clock=fixed_clock)
        result = ask(service)
        self.assertEqual(result.answer['summary'], CLAIM)
        self.assertEqual(requests(transport)[0]['context_tokens'], 8192)

    def test_model_verifier_with_a_generator_that_cannot_repair_is_refused_at_build_time(self):
        class GenerateOnly:
            def generate(self, **kwargs):
                return draft()

        with self.assertRaisesRegex(ValueError, 'repair'):
            AnswerService(
                retriever=self.retriever(), generator=GenerateOnly(),
                verifier=ModelAnswerVerifier(fixed_transport(FakeTransport())), clock=fixed_clock)

    def test_service_cannot_be_built_without_an_explicit_verifier(self):
        with self.assertRaises(TypeError):
            AnswerService(
                retriever=self.retriever(),
                generator=TransportAnswerGenerator(fixed_transport(FakeTransport())),
                clock=fixed_clock)

    def test_verifier_without_the_repair_declaration_is_refused_at_build_time(self):
        class Incomplete:
            def prepare(self, draft):
                return draft

        with self.assertRaisesRegex(ValueError, 'repairs_rejected_drafts'):
            AnswerService(
                retriever=self.retriever(),
                generator=TransportAnswerGenerator(fixed_transport(FakeTransport())),
                verifier=Incomplete(), clock=fixed_clock)

    def test_explicit_lexical_verifier_serves_controlled_generators(self):
        transport = script(FakeTransport(), wire(draft(summary=CLAIM), ALIASES))
        result = ask(lexical_service(transport, self.retriever()))
        self.assertEqual(result.answer['sections'][0]['text'], CLAIM)

    def test_both_verifiers_accept_the_same_evidence_with_the_same_answer(self):
        sources = lambda: FixtureRetriever([evidence_fixture('source', content=CLAIM)])
        lexical = script(FakeTransport(), wire(draft(summary=CLAIM), ALIASES))
        model = script(FakeTransport(), wire(draft(summary=CLAIM), ALIASES),
                       checks(supported(), supported()))
        lexical_answer = ask(lexical_service(lexical, sources())).answer
        model_answer = ask(model_service(model, sources())).answer
        self.assertEqual(lexical_answer, model_answer)


class CitationBoundaryTests(unittest.TestCase):
    """Alias bijection, package expansion, and claim binding hold under both verifiers."""

    def sources(self, count=2):
        provenance = {
            'source_id': 'official-page', 'source_document_id': 'reviewed-page',
            'source_content_sha256': 'a' * 64, 'normalized_document_sha256': 'b' * 64,
            'normalized_extraction_sha256': 'c' * 64,
            'official_url': 'https://www.nyidanmark.dk/example',
            'final_url': 'https://www.nyidanmark.dk/example', 'language': 'en-GB',
            'corpus_identity': 'kr-fixture', 'knowledge_release_id': 'kr-fixture',
        }
        return [{**evidence_fixture(key, content=CLAIM), **provenance}
                for key in ('first', 'second')[:count]]

    def answers(self, citations, *, witnesses):
        sources = self.sources()
        proposed = {'summary': CLAIM, 'sections': [
            {'kind': 'official_fact', 'text': CLAIM, 'citation_ids': citations}]}
        cited = {'verdict': 'supported', 'failure_reason': 'none',
                 'witnesses': [{'quote_id': quote} for quote in witnesses]}
        verdict = checks(cited, cited)
        results = {}
        for name, factory, extra in (
                ('lexical', lexical_service, ()), ('model', model_service, (verdict,))):
            transport = script(FakeTransport(), proposed, *extra)
            results[name] = (ask(factory(transport, FixtureRetriever(deepcopy(sources)))).answer, transport)
        return results

    def test_individual_alias_maps_to_exactly_its_canonical_citation(self):
        for name, (answer, _) in self.answers(['e2'], witnesses=['e2s1']).items():
            with self.subTest(verifier=name):
                self.assertEqual(answer['sections'][0]['citation_ids'], ['second'])

    def test_package_label_expands_to_every_declared_member(self):
        for name, (answer, transport) in self.answers(['g1'], witnesses=['e1s1', 'e2s1']).items():
            with self.subTest(verifier=name):
                self.assertEqual(answer['sections'][0]['citation_ids'], ['first', 'second'])
                sent = json.loads(requests(transport)[0]['messages'][1]['content'])
                self.assertEqual(sent['explicit_citation_packages'], {'g1': ['e1', 'e2']})
                enum = requests(transport)[0]['schema']['properties']['sections']['items'][
                    'properties']['citation_ids']['items']['enum']
                self.assertEqual(enum, ['e1', 'e2', 'g1'])

    def test_unknown_canonical_duplicate_and_overlapping_labels_fail_closed_before_verification(self):
        for citations in (['e3'], ['first'], ['e1', 'e1'], ['g1', 'e1'], ['g9'], [7]):
            for factory in (lexical_service, model_service):
                transport = script(FakeTransport(), {'summary': CLAIM, 'sections': [
                    {'kind': 'official_fact', 'text': CLAIM, 'citation_ids': citations}]})
                with self.subTest(citations=citations, factory=factory.__name__):
                    with self.assertRaises(AnswerValidationError):
                        ask(factory(transport, FixtureRetriever(self.sources())))
                    self.assertEqual(len(requests(transport)), 1)

    def test_verified_claim_binding_rejects_text_changed_after_verification(self):
        from danish_rag.answer_verification import claim_binding
        sources = self.sources(1)
        approved = claim_binding(CLAIM, ['first'], sources)
        class SwappingVerifier(ModelAnswerVerifier):
            def verified_claims(self, draft, *, evidence, configuration):
                return frozenset({approved})
        changed = wire(
            {'summary': CLAIM, 'sections': [
                {'kind': 'official_fact', 'text': 'Registration is free.', 'citation_ids': ['first']}]},
            {'first': 'e1'})
        transport = script(FakeTransport(), changed, changed)
        service = AnswerService(
            retriever=FixtureRetriever(sources),
            generator=TransportAnswerGenerator(fixed_transport(transport)),
            verifier=SwappingVerifier(fixed_transport(transport)), clock=fixed_clock)
        with self.assertRaises(AnswerValidationError):
            ask(service)


class OllamaRequestBodyTests(unittest.TestCase):
    """Bodies sent through the real Ollama adapter equal what the pipeline sent before."""

    def test_generation_verification_and_repair_bodies_keep_options_think_and_format(self):
        sent = []
        replies = iter([
            wire(draft(), ALIASES), checks(unsupported(), unsupported()),
            wire(draft(), ALIASES), checks(supported(), supported()),
        ])

        def fake_request(self, method, path, payload=None, *, missing_statuses=()):
            sent.append((method, path, json.loads(json.dumps(payload))))
            return {'message': {'content': json.dumps(next(replies))}, 'done_reason': 'stop'}

        config = configuration()
        with patch.object(OllamaTransport, '_request', fake_request):
            transport = OllamaTransport(config.endpoint, timeout_seconds=1)
            result = ask(model_service(transport, FixtureRetriever(
                [evidence_fixture('source', content=CLAIM)])))
        self.assertEqual(result.answer['sections'][0]['text'], CLAIM)
        self.assertEqual([item[:2] for item in sent], [('POST', '/api/chat')] * 4)
        generation, verification, repair, second = [item[2] for item in sent]
        for body in (generation, verification, repair, second):
            self.assertEqual(set(body), {'model', 'messages', 'stream', 'format', 'think', 'options'})
            self.assertEqual(body['model'], config.model)
            self.assertIs(body['stream'], False)
            self.assertIs(body['think'], False)
            self.assertEqual(body['options']['temperature'], 0)
            self.assertEqual(body['options']['seed'], 0)
        self.assertEqual(generation['options'], {'temperature': 0, 'seed': 0})
        for body in (verification, repair, second):
            self.assertEqual(body['options'], {'temperature': 0, 'seed': 0, 'num_ctx': 8192})
        self.assertIn('checks', verification['format']['properties'])
        self.assertIn('summary', generation['format']['properties'])
        self.assertEqual(repair['format'], generation['format'])


class OpenAICompatibleRequestTests(unittest.TestCase):
    """Strict structured output and the alias boundary hold over the OpenAI-compatible adapter."""

    def run_with_replies(self, call, replies):
        sent = []
        queue = iter(replies)

        def fake_request(self, method, path, payload=None, *, missing_statuses=()):
            sent.append((method, path, json.loads(json.dumps(payload))))
            return {'choices': [{'message': {'content': json.dumps(next(queue))}}]}

        with patch.object(OpenAICompatibleTransport, '_request', fake_request):
            transport = OpenAICompatibleTransport('http://127.0.0.1:1234', timeout_seconds=1)
            self.source = fixed_transport(transport)
            return call(TransportAnswerGenerator(self.source)), sent

    def test_social_conversation_uses_a_strict_schema(self):
        from danish_rag.social_conversation import TransportSocialReplies, conversation_schema
        schema = conversation_schema()
        result, sent = self.run_with_replies(
            lambda generator: TransportSocialReplies(self.source).converse(
                question='How are you?', configuration=provider_configuration(), schema=schema),
            [{'response': 'Fine.'}])
        self.assertEqual(result, {'response': 'Fine.'})
        method, path, body = sent[0]
        self.assertEqual((method, path), ('POST', '/v1/chat/completions'))
        self.assertEqual(body['temperature'], 0)
        format_ = body['response_format']['json_schema']
        self.assertIs(format_['strict'], True)
        self.assertEqual(format_['schema'], schema)

    def test_generation_uses_the_identical_alias_boundary(self):
        from danish_rag.answer_pipeline import answer_schema
        sources = [evidence_fixture('official-page-with-shared-prefix-' + suffix, content=CLAIM)
                   for suffix in ('first', 'second')]
        proposed = {'summary': CLAIM, 'sections': [
            {'kind': 'official_fact', 'text': CLAIM, 'citation_ids': ['e2']}]}
        result, sent = self.run_with_replies(
            lambda generator: generator.generate(
                question=QUESTION, normalized_question=QUESTION, evidence=sources,
                configuration=provider_configuration(),
                schema=answer_schema([source['citation_id'] for source in sources])),
            [proposed])
        self.assertEqual(result['sections'][0]['citation_ids'], [sources[1]['citation_id']])
        schema = sent[0][2]['response_format']['json_schema']['schema']
        enum = schema['properties']['sections']['items']['properties']['citation_ids']['items']['enum']
        self.assertEqual(enum, ['e1', 'e2'])
        shown = json.loads(sent[0][2]['messages'][1]['content'])['approved_official_evidence']
        self.assertEqual([item['citation_id'] for item in shown], ['e1', 'e2'])


if __name__ == '__main__':
    unittest.main()
