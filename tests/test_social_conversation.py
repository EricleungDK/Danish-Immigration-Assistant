"""Social conversation (#76): greetings and non-domain replies, apart from the answer module."""

import json
import re
import unittest
from pathlib import Path

from danish_rag import answer_pipeline
from danish_rag.answer_pipeline import (
    AnswerPipelineError, AnswerValidationError, TransportAnswerGenerator, fixed_transport,
)
from danish_rag.local_model_transport import FakeTransport, ModelUnreachableError
from danish_rag.social_conversation import (
    SocialConversation, TransportSocialReplies, classify_conversation_turn, conversation_schema,
)
from tests.answer_transport import requests, script
from tests.test_issue_13_evidence_safety import provider_configuration

ROOT = Path(__file__).resolve().parents[1]


def social(transport):
    return SocialConversation(TransportSocialReplies(fixed_transport(transport)))


def reply(transport, question='Tell me a short joke.', kind='social'):
    return social(transport).reply(
        question, provider_configuration(), kind=kind, corpus_identity='corpus-1')


class ClassificationTests(unittest.TestCase):
    def test_exact_greetings(self):
        for text in ('hi', 'Hello there!', 'good morning', 'HEY.'):
            self.assertEqual(classify_conversation_turn(text), 'greeting', text)

    def test_social_and_domain_split(self):
        self.assertEqual(classify_conversation_turn('Tell me a short joke.'), 'social')
        self.assertEqual(classify_conversation_turn('What is the capital of France?'), 'social')
        self.assertEqual(classify_conversation_turn('What is PD2?'), 'answer')

    def test_follow_up_is_an_answer_only_after_a_domain_turn(self):
        domain = [{'question': 'What is PD2?', 'answer': {'response_kind': 'answer'}}]
        chat = [{'question': 'hi', 'answer': {'response_kind': 'conversation'}}]
        self.assertEqual(
            classify_conversation_turn('What about that?', conversation_turns=domain), 'answer')
        self.assertEqual(
            classify_conversation_turn('What about that?', conversation_turns=chat), 'social')


class ReplyTests(unittest.TestCase):
    def test_greeting_is_local_and_never_touches_the_transport(self):
        transport = FakeTransport()
        result = reply(transport, 'hi', kind='greeting')
        self.assertEqual(requests(transport), [])
        self.assertEqual(result.answer['response_kind'], 'conversation')
        self.assertIs(result.answer['generation_used'], False)
        self.assertIn('Hello', result.answer['summary'])
        self.assertEqual(result.corpus_identity, 'corpus-1')
        self.assertEqual(result.question, 'hi')

    def test_social_reply_uses_the_transport_with_the_fact_bounded_schema(self):
        transport = script(FakeTransport(), {'response': ' Why did the tomato blush? '})
        result = reply(transport)
        sent = requests(transport)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['schema'], conversation_schema())
        self.assertEqual(sent[0]['model'], provider_configuration().model)
        self.assertIsNone(sent[0]['context_tokens'])
        system, user = sent[0]['messages']
        self.assertIn('non-factual social conversation', system['content'])
        self.assertIn('Do not provide immigration', system['content'])
        self.assertEqual(json.loads(user['content']), {
            'conversation_turn': 'Tell me a short joke.',
            'required_output_schema': conversation_schema()})
        self.assertNotIn('approved_official_evidence', user['content'])
        self.assertEqual(result.answer, {
            'summary': 'Why did the tomato blush?', 'response_kind': 'conversation',
            'generation_used': True, 'assumptions': [], 'sections': [], 'citations': [],
            'suggested_follow_ups': [],
            'trust': {
                'evidence_confidence': 'Not applicable',
                'evidence_confidence_reason':
                    'This was a social conversation turn, not an official factual answer.',
                'fresh_tomato_score': 'Not applicable',
                'fresh_tomato_reason':
                    'No material source was needed for this social conversation turn.',
            },
        })

    def test_invalid_replies_are_rejected(self):
        for body in ({'response': 'ok', 'extra': 1}, {'response': '  '}, {'response': 3}):
            with self.assertRaises(AnswerValidationError, msg=body):
                reply(script(FakeTransport(), body))

    def test_transport_failure_maps_to_pipeline_error(self):
        transport = FakeTransport()
        transport.queue_error(ModelUnreachableError('down'))
        with self.assertRaises(AnswerPipelineError):
            reply(transport)

    def test_provider_without_converse_is_refused(self):
        with self.assertRaisesRegex(AnswerPipelineError, 'social conversation'):
            SocialConversation(object()).reply(
                'joke', provider_configuration(), kind='social', corpus_identity='c')


class AnswerModuleBoundaryTests(unittest.TestCase):
    def test_answer_module_has_no_social_mode_code(self):
        source = (ROOT / 'danish_rag' / 'answer_pipeline.py').read_text(encoding='utf-8')
        self.assertIsNone(re.search(r'social|greeting|converse|conversation_schema', source, re.I))
        self.assertFalse(hasattr(TransportAnswerGenerator, 'converse'))
        self.assertFalse(hasattr(answer_pipeline, 'classify_conversation_turn'))
