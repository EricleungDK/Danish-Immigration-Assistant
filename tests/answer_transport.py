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
from danish_rag.controlled_model import CannedAnswer, ControlledModel
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


def model_generator(model: Any) -> TransportAnswerGenerator:
    """The production generator over a fake served by a controlled model."""

    return TransportAnswerGenerator(fixed_transport(FakeTransport(responder=model)))


def requests(transport: FakeTransport) -> list[dict[str, Any]]:
    return [recorded.params for recorded in transport.requests]


def scripted_generator(*responses: Any) -> tuple[TransportAnswerGenerator, FakeTransport]:
    """The production generator over its own scripted fake (generation-level tests)."""

    transport = script(FakeTransport(), *responses)
    return TransportAnswerGenerator(fixed_transport(transport)), transport


class _EvidenceSpy:
    """Wraps a retriever and remembers the evidence it returned last."""

    def __init__(self, retriever: Any) -> None:
        self._retriever = retriever
        self.last_evidence: list[dict[str, Any]] = []

    @property
    def manifest(self) -> Any:
        return self._retriever.manifest

    def retrieve(self, question: str) -> list[dict[str, Any]]:
        self.last_evidence = self._retriever.retrieve(question)
        return self.last_evidence


class CannedModel:
    """A fixed answer payload, written with the retriever's own citation ids.

    `service(retriever)` runs the production generator over the shared fake with the
    deterministic lexical guard; `calls` records what the model was asked.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self._canned: CannedAnswer | None = None

    def service(self, retriever: Any, *, clock=fixed_clock) -> AnswerService:
        spy = _EvidenceSpy(retriever)
        self._canned = CannedAnswer(self.payload, lambda question: spy.last_evidence)
        return lexical_service(
            FakeTransport(responder=ControlledModel(draft=self._canned)), spy, clock=clock)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._canned.calls if self._canned else []
