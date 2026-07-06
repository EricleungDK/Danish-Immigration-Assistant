"""Evidence-bounded answer generation and validation."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from .provider_setup import ProviderConfiguration
from .retrieval import normalize_question


class AnswerPipelineError(RuntimeError):
    """Raised when the answer path cannot complete safely."""


class AnswerValidationError(AnswerPipelineError):
    """Raised when generated structured output is not evidence-bounded."""


class TrustLevel(Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


CONFLICTING_AGREEMENT_STATES = {"conflict", "conflicts", "conflicting", "contradicts"}


class AnswerGenerator(Protocol):
    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class AnswerResult:
    question: str
    normalized_question: str
    answer: dict[str, Any]
    model_identity: dict[str, Any]
    corpus_identity: str


def answer_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "sections": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["official_fact", "interpretation", "refusal"],
                        },
                        "text": {"type": "string"},
                        "citation_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["kind", "text", "citation_ids"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "sections"],
        "additionalProperties": False,
    }


class LocalProviderAnswerGenerator:
    """Structured answer generator for configured loopback providers."""

    def __init__(self, *, timeout_seconds: float = 90.0) -> None:
        self.timeout_seconds = timeout_seconds

    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        if configuration.provider_id == "ollama":
            return self._generate_ollama(
                question=question,
                normalized_question=normalized_question,
                evidence=evidence,
                configuration=configuration,
                schema=schema,
            )
        if configuration.provider_id == "openai_compatible":
            return self._generate_openai_compatible(
                question=question,
                normalized_question=normalized_question,
                evidence=evidence,
                configuration=configuration,
                schema=schema,
            )
        raise AnswerPipelineError("Configured generation provider is unsupported.")

    def _generate_ollama(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "model": configuration.model,
            "messages": _answer_messages(question, normalized_question, evidence),
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
        }
        response = self._request_json(configuration.endpoint, "POST", "/api/chat", payload)
        content = response.get("message", {}).get("content")
        return _parse_provider_content(content)

    def _generate_openai_compatible(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "model": configuration.model,
            "messages": _answer_messages(question, normalized_question, evidence),
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "danish_rag_answer",
                    "schema": schema,
                    "strict": True,
                },
            },
        }
        response = self._request_json(
            configuration.endpoint,
            "POST",
            "/v1/chat/completions",
            payload,
        )
        content = (
            response.get("choices", [{}])[0]
            .get("message", {})
            .get("content")
        )
        return _parse_provider_content(content)

    def _request_json(
        self,
        endpoint: str,
        method: str,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{endpoint.rstrip('/')}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AnswerPipelineError(f"Local provider returned HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise AnswerPipelineError(f"Local provider failed during answer generation: {exc}") from exc


class AnswerService:
    def __init__(
        self,
        *,
        retriever: Any,
        generator: AnswerGenerator,
    ) -> None:
        self.retriever = retriever
        self.generator = generator

    def answer(self, question: str, configuration: ProviderConfiguration) -> AnswerResult:
        normalized_question = normalize_question(question)
        evidence = self.retriever.retrieve(question)
        if not evidence:
            raise AnswerValidationError(
                "No approved official evidence was retrieved for this question."
            )
        generated = self.generator.generate(
            question=question,
            normalized_question=normalized_question,
            evidence=evidence,
            configuration=configuration,
            schema=answer_schema(),
        )
        answer = validate_answer(generated, evidence=evidence)
        return AnswerResult(
            question=question,
            normalized_question=normalized_question,
            answer=answer,
            model_identity=_model_identity(configuration),
            corpus_identity=str(self.retriever.manifest["corpus_id"]),
        )


def validate_answer(
    payload: dict[str, Any],
    *,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AnswerValidationError("Structured answer was not a JSON object.")
    summary = payload.get("summary")
    sections = payload.get("sections")
    if not isinstance(summary, str) or not summary.strip():
        raise AnswerValidationError("Structured answer is missing a summary.")
    if not isinstance(sections, list) or not sections:
        raise AnswerValidationError("Structured answer is missing answer sections.")

    evidence_by_citation_id = {item["citation_id"]: item for item in evidence}
    sanitized_sections: list[dict[str, Any]] = []
    used_citation_ids: set[str] = set()
    official_fact_count = 0
    cited_official_fact_count = 0
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            raise AnswerValidationError(f"Answer section {index} was not an object.")
        kind = section.get("kind")
        text = section.get("text")
        citation_ids = section.get("citation_ids")
        if kind not in {"official_fact", "interpretation", "refusal"}:
            raise AnswerValidationError(f"Answer section {index} has an unsupported kind.")
        if not isinstance(text, str) or not text.strip():
            raise AnswerValidationError(f"Answer section {index} is missing text.")
        if not isinstance(citation_ids, list):
            raise AnswerValidationError(f"Answer section {index} citation_ids must be a list.")
        normalized_citation_ids = [str(citation_id) for citation_id in citation_ids]
        unknown_citation_ids = sorted(set(normalized_citation_ids) - set(evidence_by_citation_id))
        if unknown_citation_ids:
            raise AnswerValidationError(
                "Answer cited material that was not retrieved: "
                f"{', '.join(unknown_citation_ids)}"
            )
        if kind == "official_fact":
            official_fact_count += 1
            if not normalized_citation_ids:
                raise AnswerValidationError(
                    "Answer validation failed: every official fact needs an adjacent citation."
                )
            cited_official_fact_count += 1
        used_citation_ids.update(normalized_citation_ids)
        sanitized_sections.append(
            {
                "kind": kind,
                "label": _section_label(kind),
                "text": text.strip(),
                "citation_ids": normalized_citation_ids,
                "citations": [
                    _citation_from_evidence(evidence_by_citation_id[citation_id])
                    for citation_id in normalized_citation_ids
                ],
            }
        )
    if official_fact_count == 0:
        raise AnswerValidationError("Answer validation failed: no official fact was produced.")

    used_evidence = [
        evidence_by_citation_id[citation_id]
        for citation_id in used_citation_ids
    ]
    trust = _trust_indicators(
        official_fact_count=official_fact_count,
        cited_official_fact_count=cited_official_fact_count,
        used_evidence=used_evidence,
    )
    return {
        "summary": summary.strip(),
        "sections": sanitized_sections,
        "citations": _material_sources(sanitized_sections, used_evidence),
        "trust": trust,
    }


def _answer_messages(
    question: str,
    normalized_question: str,
    evidence: list[dict[str, Any]],
) -> list[dict[str, str]]:
    evidence_payload = [
        {
            "citation_id": item["citation_id"],
            "title": item["title"],
            "publisher": item["publisher"],
            "official_url": item["official_url"],
            "checked_at_utc": item["checked_at_utc"],
            "content": item["content"],
        }
        for item in evidence
    ]
    return [
        {
            "role": "system",
            "content": (
                "Return only JSON matching the provided schema. Answer in English, preserve "
                "Danish terms, cite each official fact with retrieved citation_ids, and do "
                "not add official facts outside the evidence."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": question,
                    "normalized_question": normalized_question,
                    "approved_official_evidence": evidence_payload,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    ]


def _parse_provider_content(content: Any) -> dict[str, Any]:
    if not isinstance(content, str):
        raise AnswerValidationError("Local provider response did not contain JSON text.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AnswerValidationError(
            f"Local provider response was not valid structured JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise AnswerValidationError("Local provider structured answer was not a JSON object.")
    return parsed


def _citation_from_evidence(evidence: dict[str, Any]) -> dict[str, str]:
    checked_at = str(evidence["checked_at_utc"])
    fresh_tomato_score, fresh_tomato_reason = _fresh_tomato_indicator(evidence)
    return {
        "citation_id": str(evidence["citation_id"]),
        "title": str(evidence["title"]),
        "publisher": str(evidence["publisher"]),
        "official_url": str(evidence["official_url"]),
        "checked_at_utc": checked_at,
        "checked_at_display": checked_at[:10],
        "corpus_identity": str(evidence["knowledge_release_id"]),
        "fresh_tomato_score": fresh_tomato_score.value,
        "fresh_tomato_reason": fresh_tomato_reason,
    }


def _material_sources(
    sections: list[dict[str, Any]],
    used_evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for item in sorted(used_evidence, key=lambda evidence_item: evidence_item["citation_id"]):
        citation = _citation_from_evidence(item)
        citation["claim_support"] = [
            {
                "label": section["label"],
                "text": section["text"],
            }
            for section in sections
            if citation["citation_id"] in section["citation_ids"]
        ]
        sources.append(citation)
    return sources


def _trust_indicators(
    *,
    official_fact_count: int,
    cited_official_fact_count: int,
    used_evidence: list[dict[str, Any]],
) -> dict[str, str]:
    source_scores = [_fresh_tomato_indicator(item)[0] for item in used_evidence]
    fresh_tomato_score = _lowest_indicator(source_scores)
    fresh_reason = _fresh_tomato_reason(fresh_tomato_score)
    conflicting_sources = [
        str(item["citation_id"])
        for item in used_evidence
        if _is_conflicting_evidence(item)
    ]
    coverage_complete = (
        bool(official_fact_count)
        and cited_official_fact_count == official_fact_count
    )
    if coverage_complete and not conflicting_sources:
        evidence_confidence = TrustLevel.HIGH
        evidence_reason = (
            f"Evidence coverage is complete for {official_fact_count} official fact(s); "
            "cited material sources are attached directly to the claims and no retrieved "
            "material source is marked as conflicting."
        )
    elif conflicting_sources:
        evidence_confidence = TrustLevel.LOW
        evidence_reason = (
            "Evidence agreement is incomplete: conflicting material source(s) were retrieved "
            f"for this answer ({', '.join(sorted(conflicting_sources))})."
        )
    else:
        evidence_confidence = TrustLevel.LOW
        evidence_reason = (
            f"Evidence coverage is incomplete: {cited_official_fact_count} of "
            f"{official_fact_count} official fact(s) have adjacent approved-source citations."
        )
    return {
        "evidence_confidence": evidence_confidence.value,
        "evidence_confidence_reason": evidence_reason,
        "fresh_tomato_score": fresh_tomato_score.value,
        "fresh_tomato_reason": fresh_reason,
    }


def _is_conflicting_evidence(evidence: dict[str, Any]) -> bool:
    if evidence.get("conflicts_with_answer") is True:
        return True
    agreement_state = str(evidence.get("agreement_state", "supports")).casefold()
    return agreement_state in CONFLICTING_AGREEMENT_STATES


def _fresh_tomato_indicator(evidence: dict[str, Any]) -> tuple[TrustLevel, str]:
    source_health = str(evidence.get("source_health", "unknown"))
    if source_health == "healthy":
        return (
            TrustLevel.HIGH,
            "Source freshness is high: the material source is current and healthy.",
        )
    if source_health == "overdue-policy-usable":
        return (
            TrustLevel.MEDIUM,
            "Source freshness is medium: the material source is policy-usable but overdue for review.",
        )
    return (
        TrustLevel.LOW,
        "Source freshness is low: the material source is not current and healthy.",
    )


def _lowest_indicator(scores: list[TrustLevel]) -> TrustLevel:
    if not scores:
        return TrustLevel.LOW
    order = {TrustLevel.LOW: 0, TrustLevel.MEDIUM: 1, TrustLevel.HIGH: 2}
    return min(scores, key=lambda score: order.get(score, 0))


def _fresh_tomato_reason(score: TrustLevel) -> str:
    if score is TrustLevel.HIGH:
        return "Source freshness is high because all material sources are current and healthy."
    if score is TrustLevel.MEDIUM:
        return (
            "Source freshness is medium because the lowest material-source freshness score "
            "is Medium."
        )
    return "Source freshness is low because no current healthy material source could be attached."


def _section_label(kind: str) -> str:
    labels = {
        "official_fact": "Official fact",
        "interpretation": "Interpretation",
        "refusal": "Evidence-bounded refusal",
    }
    return labels[kind]


def _model_identity(configuration: ProviderConfiguration) -> dict[str, Any]:
    return {
        "provider_id": configuration.provider_id,
        "endpoint": configuration.endpoint,
        "model": configuration.model,
        "provider_version": configuration.provider_version,
        "model_identity": configuration.model_identity,
        "capabilities": configuration.capabilities,
    }
