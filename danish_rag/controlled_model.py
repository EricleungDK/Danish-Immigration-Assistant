"""Controlled model: a deterministic stand-in for the local model (#77).

Serve it through the shared fake, `FakeTransport(responder=ControlledModel(...))`, to run the
real application stack (transport generation, verification, repair, social replies and
embeddings) end to end without a model. It writes the drafts it is given, verifies them
honestly (a supporting source span is chosen for every claim, and the application's own
witness checks still decide), and replies to social turns with configured text. Fixtures,
browser tests, and release monitors share it; nothing subclasses a production adapter.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .claim_support import assess_claim_support
from .local_model_transport import FakeTransportScriptError, UnsupportedOperationError

DEFAULT_EMBEDDING_DIMENSIONS = 768
_TOKEN = re.compile(r"[0-9a-zA-ZæøåÆØÅ]+")
_WITNESS_ID = re.compile(r"^e(\d+)s(\d+)(?:-s(\d+))?$")


@dataclass(frozen=True)
class GenerationCall:
    """One answer-writing request, as the model received it."""

    model: str
    question: str
    normalized_question: str
    #: The evidence as shown to the model (citation ids are the model-facing aliases).
    evidence: list[dict[str, Any]]
    schema: dict[str, Any]
    #: True when this is the bounded repair of a rejected draft.
    repair: bool
    messages: list[dict[str, str]] = field(repr=False, default_factory=list)


@dataclass(frozen=True)
class ConversationCall:
    """One social-reply request."""

    model: str
    question: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class VerificationCall:
    """One evidence-verification request."""

    model: str
    proposed_answer: dict[str, Any]


class ControlledModel:
    """The responder for `FakeTransport(responder=...)`; records what it was asked."""

    def __init__(
        self,
        *,
        draft: Callable[[GenerationCall], dict[str, Any]] | None = None,
        conversation_response: str | Callable[[ConversationCall], str] = (
            "Happy to chat. Ask me about the approved Danish language-requirement sources."
        ),
        embedding_dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS,
        model_identity: dict[str, Any] | None = None,
    ) -> None:
        self._draft = draft
        self._conversation_response = conversation_response
        self.embedding_dimensions = embedding_dimensions
        self._model_identity = model_identity or {
            "digest": "sha256:controlled-model-embedding",
            "details": {"family": "gemma3", "parameter_size": "fixture"},
        }
        self.calls: list[GenerationCall] = []
        self.conversation_calls: list[ConversationCall] = []
        self.verifications: list[VerificationCall] = []

    def __call__(self, operation: str, params: dict[str, Any]) -> Any:
        if operation == "chat":
            return json.dumps(self._chat(params), ensure_ascii=False)
        if operation == "embed":
            return [self._embedding(text) for text in params["texts"]]
        if operation == "show_model":
            return {"model": params["model"], **self._model_identity}
        raise UnsupportedOperationError(
            f"The controlled model does not serve {operation}; script it on the fake.")

    # -- chat -------------------------------------------------------------------------

    def _chat(self, params: dict[str, Any]) -> dict[str, Any]:
        messages = [dict(message) for message in params["messages"]]
        user = next((message for message in messages if message["role"] == "user"), None)
        if user is None:
            raise FakeTransportScriptError("The controlled model got a request with no user message.")
        try:
            payload = json.loads(user["content"])
        except ValueError as exc:
            raise FakeTransportScriptError(
                "The controlled model got a user message that is not JSON.") from exc
        if not isinstance(payload, dict):
            raise FakeTransportScriptError("The controlled model got a user message that is not a JSON object.")
        model = params["model"]
        if "approved_official_evidence" in payload:
            if self._draft is None:
                raise FakeTransportScriptError(
                    "The controlled model was asked to write an answer but has no draft.")
            call = GenerationCall(
                model=model,
                question=payload["question"],
                normalized_question=payload["normalized_question"],
                evidence=payload["approved_official_evidence"],
                schema=payload.get("required_output_schema", {}),
                repair=len(messages) > 2 and messages[-2]["role"] == "assistant",
                messages=messages,
            )
            self.calls.append(call)
            return self._draft(call)
        if "proposed_answer" in payload:
            self.verifications.append(VerificationCall(model, payload["proposed_answer"]))
            return _verdicts(payload)
        if "conversation_turn" in payload:
            call = ConversationCall(
                model, payload["conversation_turn"], payload.get("required_output_schema", {}))
            self.conversation_calls.append(call)
            reply = self._conversation_response
            return {"response": reply(call) if callable(reply) else reply}
        raise FakeTransportScriptError("The controlled model did not recognise this request.")

    # -- embeddings -------------------------------------------------------------------

    def _embedding(self, text: str) -> list[float]:
        vector = [0.0] * self.embedding_dimensions
        for token in _TOKEN.findall(text.casefold()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.embedding_dimensions
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [round(value / norm, 9) for value in vector]


class CannedAnswer:
    """A draft that returns a fixed payload written with the retriever's own citation ids.

    The model is shown aliased evidence, so the canonical ids are recovered by matching the
    evidence text it was shown against `sources` (a list, or a function of the question).
    `calls` records each request with the canonical ids of the evidence the model saw.
    """

    def __init__(
        self,
        payload: dict[str, Any],
        sources: list[dict[str, Any]] | Callable[[str], list[dict[str, Any]]],
    ) -> None:
        self.payload = payload
        self._sources = sources
        self.calls: list[dict[str, Any]] = []

    def __call__(self, call: GenerationCall) -> dict[str, Any]:
        sources = self._sources(call.question) if callable(self._sources) else self._sources
        canonical_by_content: dict[str, str] = {}
        for item in sources:
            content, citation_id = str(item["content"]), str(item["citation_id"])
            if canonical_by_content.setdefault(content, citation_id) != citation_id:
                raise FakeTransportScriptError(
                    f"Sources {canonical_by_content[content]!r} and {citation_id!r} have "
                    "identical content; the canned answer cannot tell them apart.")
        aliases = {
            canonical_by_content[str(item["content"])]: str(item["citation_id"])
            for item in call.evidence
        }
        canonical_by_alias = {alias: canonical for canonical, alias in aliases.items()}
        offered = call.schema["properties"]["sections"]["items"]["properties"][
            "citation_ids"]["items"].get("enum", [])
        self.calls.append({
            "question": call.question,
            "normalized_question": call.normalized_question,
            "evidence_ids": list(aliases),
            #: The canonical ids the model was allowed to cite, in the schema's order.
            "schema_ids": [canonical_by_alias[a] for a in offered if a in canonical_by_alias],
            "schema": call.schema,
        })
        payload = json.loads(json.dumps(self.payload))
        for section in payload.get("sections", []):
            section["citation_ids"] = [
                aliases.get(key, key) for key in section.get("citation_ids", [])
            ]
        return payload


def _verdicts(payload: dict[str, Any]) -> dict[str, Any]:
    """Verify each item the way the deterministic claim-support guard would.

    An official fact is supported only when `assess_claim_support` finds it in the cited
    source text; every other item is approved. A supported item names the widest allowed
    source window per cited source, which keeps every governing qualifier in view. The
    application's own witness checks still validate what is returned.
    """

    schema = payload["required_output_schema"]
    answer = payload["proposed_answer"]
    source_text = {
        source["citation_id"]: " ".join(source["spans"].values())
        for source in payload["source_witnesses"]
    }
    items = [{"kind": "summary", "text": answer["summary"], "citation_ids": []},
             *answer["sections"]]
    checks = {}
    for index, (item, spec) in enumerate(schema["properties"]["checks"]["properties"].items()):
        entry = items[index]
        branches = {
            branch["properties"]["verdict"]["enum"][0]: branch for branch in spec["anyOf"]
        }
        cited = [source_text[key] for key in entry["citation_ids"] if key in source_text]
        if entry["kind"] == "official_fact" and not assess_claim_support(
                entry["text"], cited).supported:
            checks[item] = {
                "verdict": "unsupported", "failure_reason": "unsupported_detail", "witnesses": []}
        elif "supported" in branches:
            allowed = branches["supported"]["properties"]["witnesses"]["items"][
                "properties"]["quote_id"]["enum"]
            checks[item] = {
                "verdict": "supported", "failure_reason": "none",
                "witnesses": [{"quote_id": quote_id} for quote_id in _widest(allowed)],
            }
        elif "non_factual" in branches:
            checks[item] = {"verdict": "non_factual", "failure_reason": "none", "witnesses": []}
        else:
            checks[item] = {"verdict": "unsupported", "failure_reason": "uncertain", "witnesses": []}
    return {"checks": checks}


def _widest(quote_ids: list[str]) -> list[str]:
    best: dict[int, tuple[int, str]] = {}
    for quote_id in quote_ids:
        match = _WITNESS_ID.match(quote_id)
        if match is None:
            continue
        source, first = int(match.group(1)), int(match.group(2))
        width = int(match.group(3) or first) - first
        if source not in best or width > best[source][0]:
            best[source] = (width, quote_id)
    return [quote_id for _, quote_id in (best[source] for source in sorted(best))]
