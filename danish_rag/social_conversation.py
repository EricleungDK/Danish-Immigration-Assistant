"""Social conversation: greetings and clearly non-domain replies (#76).

A small module beside the Evidence-bounded answer. An exact greeting is answered locally
(no retrieval, no generation); any other non-domain turn gets one fact-bounded reply from
the Local model transport. A social turn carries no corpus evidence, verification, or Trust
indicators. The answer module knows nothing about this: the application routes a turn here
first and only domain turns reach `AnswerService`.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from .answer_pipeline import (
    AnswerPipelineError,
    AnswerResult,
    AnswerValidationError,
    TransportSource,
    configuration_model_identity,
    needs_follow_up_context,
    structured_chat,
)
from .provider_setup import ProviderConfiguration
from .retrieval import normalize_question


def conversation_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "response": {"type": "string", "minLength": 1},
        },
        "required": ["response"],
        "additionalProperties": False,
    }


def classify_conversation_turn(
    question: str,
    *,
    conversation_turns: list[dict[str, Any]] | None = None,
) -> str:
    compact = " ".join(question.split()).casefold()
    greeting_patterns = (
        r"(?:hi|hello|hey)(?: there)?[!.?]*",
        r"good (?:morning|afternoon|evening)[!.?]*",
    )
    if any(re.fullmatch(pattern, compact) for pattern in greeting_patterns):
        return "greeting"

    social_patterns = (
        r"(?:thanks|thank you)(?: very much| a lot)?(?:,? that helps)?[!.?]*",
        r"(?:how are you|how is it going|how's it going)[!.?]*",
        r"(?:tell me (?:a|another) (?:short )?joke|make me laugh)[!.?]*",
        r"(?:bye|goodbye|see you|talk to you later)[!.?]*",
        r"(?:nice to meet you|can we chat|let's chat)[!.?]*",
    )
    if any(re.fullmatch(pattern, compact) for pattern in social_patterns):
        return "social"
    if _mentions_supported_domain(compact):
        return "answer"
    has_domain_context = any(
        turn.get("answer", {}).get("response_kind") != "conversation"
        for turn in conversation_turns or []
    )
    if has_domain_context and needs_follow_up_context(question):
        return "answer"
    return "social"


def _mentions_supported_domain(lookup: str) -> bool:
    domain_terms = (
        "danish",
        "dansk",
        "prøve",
        "prove i dansk",
        "pd1",
        "pd2",
        "pd3",
        "studieprøven",
        "studieproven",
        "permanent residence",
        "permanent ophold",
        "opholdstilladelse",
        "citizenship",
        "statsborgerskab",
        "siri",
        "nyidanmark",
        "sprogcenter",
        "language requirement",
        "language exam",
        "language test",
        "register",
        "registration",
        "certificate",
        "diploma",
        "knowledge release",
        "corpus",
        "evidence",
        "official source",
        "official page",
    )
    if any(term in lookup for term in domain_terms):
        return True
    return re.search(r"\b(?:exam|test)\b", lookup) is not None


def _greeting_answer() -> dict[str, Any]:
    return _conversation_answer(
        "Hello! I can chat briefly and help explain approved official information "
        "about Danish permanent-residence language requirements and Danish language "
        "examinations. What would you like to talk about?",
        generation_used=False,
    )


def _generated_conversation_answer(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"response"}:
        raise AnswerValidationError(
            "Structured social response must contain exactly one response field."
        )
    response = payload.get("response")
    if not isinstance(response, str) or not response.strip():
        raise AnswerValidationError(
            "Structured social response is missing non-empty response text."
        )
    return _conversation_answer(response.strip(), generation_used=True)


def _conversation_answer(text: str, *, generation_used: bool) -> dict[str, Any]:
    return {
        "summary": text,
        "response_kind": "conversation",
        "generation_used": generation_used,
        "assumptions": [],
        "sections": [],
        "citations": [],
        "suggested_follow_ups": [],
        "trust": {
            "evidence_confidence": "Not applicable",
            "evidence_confidence_reason": (
                "This was a social conversation turn, not an official factual answer."
            ),
            "fresh_tomato_score": "Not applicable",
            "fresh_tomato_reason": (
                "No material source was needed for this social conversation turn."
            ),
        },
    }


def _conversation_messages(
    question: str,
    *,
    schema: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Return only JSON matching required_output_schema. Respond naturally, "
                "briefly, and in English to this non-factual social conversation turn. "
                "Do not provide immigration, legal, eligibility, examination, current-event, "
                "or other external factual claims. Do not imply that the generation model "
                "is an approved official source. If the request requires factual information, "
                "state that factual answers are limited to the installed corpus of approved "
                "official sources and invite a supported Danish-language-requirement or "
                "examination question."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "conversation_turn": question,
                    "required_output_schema": schema,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    ]


class SocialReplies(Protocol):
    """Writes one structured social reply (`conversation_schema()`) for a turn."""

    def converse(
        self,
        *,
        question: str,
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class TransportSocialReplies:
    """Social replies written by the configured provider over a Local model transport."""

    def __init__(self, transports: TransportSource) -> None:
        self._transports = transports

    def converse(
        self,
        *,
        question: str,
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        return structured_chat(
            self._transports, configuration,
            _conversation_messages(question, schema=schema), schema,
        )


class SocialConversation:
    """Turns a greeting or social question into a stored-shape conversation answer."""

    def __init__(self, replies: SocialReplies) -> None:
        self.replies = replies

    def reply(
        self,
        question: str,
        configuration: ProviderConfiguration,
        *,
        kind: str,
        corpus_identity: str,
    ) -> AnswerResult:
        """`kind` is "greeting" (answered locally) or "social" (one transport reply)."""

        if kind == "greeting":
            answer = _greeting_answer()
        elif kind == "social":
            converse = getattr(self.replies, "converse", None)
            if not callable(converse):
                raise AnswerPipelineError(
                    "Configured generation provider does not support bounded social conversation."
                )
            answer = _generated_conversation_answer(converse(
                question=question,
                configuration=configuration,
                schema=conversation_schema(),
            ))
        else:
            raise ValueError(f"Not a social conversation turn: {kind!r}")
        return AnswerResult(
            question=question,
            normalized_question=normalize_question(question),
            answer=answer,
            model_identity=configuration_model_identity(configuration),
            corpus_identity=corpus_identity,
        )
