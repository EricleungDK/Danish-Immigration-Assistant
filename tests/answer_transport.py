"""Scripted-transport helpers for Evidence-bounded answer tests (#75).

Tests script generation, verification, and repair responses in order on the shared
`FakeTransport` and read the requests it recorded; nothing here subclasses a production
generator or reaches past the transport.
"""

from __future__ import annotations

import json
from typing import Any

from danish_rag.answer_pipeline import (
    AnswerService,
    LexicalAnswerVerifier,
    ModelAnswerVerifier,
    TransportAnswerGenerator,
    fixed_transport,
)
from danish_rag.local_model_transport import FakeTransport
from tests.evaluation_time import fixed_clock


def wire(answer: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    """The model-facing form of a canonical answer (citation aliases e1, e2, ...)."""

    return {
        **answer,
        "sections": [
            {**section, "citation_ids": [aliases.get(key, key) for key in section["citation_ids"]]}
            for section in answer["sections"]
        ],
    }


def script(transport: FakeTransport, *responses: Any) -> FakeTransport:
    """Queue chat responses in order; dicts are serialised as the model's JSON."""

    for response in responses:
        transport.queue_chat(response if isinstance(response, str) else json.dumps(response))
    return transport


def model_service(transport: FakeTransport, retriever: Any, *, clock=fixed_clock) -> AnswerService:
    """Production regime: transport generation, model verification and repair."""

    source = fixed_transport(transport)
    return AnswerService(
        retriever=retriever,
        generator=TransportAnswerGenerator(source),
        verifier=ModelAnswerVerifier(source),
        clock=clock,
    )


def lexical_service(transport: FakeTransport, retriever: Any, *, clock=fixed_clock) -> AnswerService:
    """Same generator over the same transport, deterministic lexical guard."""

    return AnswerService(
        retriever=retriever,
        generator=TransportAnswerGenerator(fixed_transport(transport)),
        verifier=LexicalAnswerVerifier(),
        clock=clock,
    )


def requests(transport: FakeTransport) -> list[dict[str, Any]]:
    return [recorded.params for recorded in transport.requests]


class ScriptedGenerator(TransportAnswerGenerator):
    """A transport generator over its own scripted fake (generation-level tests)."""

    def __init__(self, *responses: Any) -> None:
        self.transport = script(FakeTransport(), *responses)
        super().__init__(fixed_transport(self.transport))

    @property
    def requests(self) -> list[dict[str, Any]]:
        return requests(self.transport)
