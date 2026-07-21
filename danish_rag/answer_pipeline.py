"""Evidence-bounded answer generation and validation."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from .claim_support import assess_claim_support
from .privacy_boundary import PrivacyBoundaryError, require_loopback_endpoint
from .provider_setup import ProviderConfiguration
from .retrieval import normalize_question
from .source_freshness import assess_source_freshness


class AnswerPipelineError(RuntimeError):
    """Raised when the answer path cannot complete safely."""


class AnswerValidationError(AnswerPipelineError):
    """Raised when generated structured output is not evidence-bounded."""


class TrustLevel(Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


CONFLICTING_AGREEMENT_STATES = {"conflict", "conflicts", "conflicting", "contradicts"}
LOW_RISK_EXAM_TERM_ASSUMPTION = (
    "You are asking for a general explanation of the Danish examination term, not "
    "a personal eligibility decision."
)


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

    def converse(
        self,
        *,
        question: str,
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


@dataclass(frozen=True)
class AmbiguityDecision:
    response_kind: str
    assumptions: list[str]
    clarification_question: str = ""
    clarification_reason: str = ""


@dataclass(frozen=True)
class SafetyDecision:
    response_kind: str
    refusal_text: str = ""
    skip_generation: bool = False


def answer_schema(citation_ids: list[str] | None = None) -> dict[str, Any]:
    citation_item_schema: dict[str, Any] = {"type": "string"}
    if citation_ids is not None:
        citation_item_schema["enum"] = sorted(set(citation_ids))
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "minLength": 1},
            "sections": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "official_fact",
                                "interpretation",
                                "refusal",
                                "source_warning",
                            ],
                        },
                        "text": {"type": "string", "minLength": 1},
                        "citation_ids": {
                            "type": "array",
                            "items": citation_item_schema,
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


def conversation_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "response": {"type": "string", "minLength": 1},
        },
        "required": ["response"],
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

    def converse(
        self,
        *,
        question: str,
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        messages = _conversation_messages(question, schema=schema)
        if configuration.provider_id == "ollama":
            return self._generate_ollama_messages(
                messages=messages,
                configuration=configuration,
                schema=schema,
            )
        if configuration.provider_id == "openai_compatible":
            return self._generate_openai_compatible_messages(
                messages=messages,
                configuration=configuration,
                schema=schema,
                schema_name="danish_rag_conversation",
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
        messages = _answer_messages(
            question,
            normalized_question,
            evidence,
            schema=schema,
        )
        return self._generate_ollama_messages(
            messages=messages,
            configuration=configuration,
            schema=schema,
        )

    def _generate_ollama_messages(
        self,
        *,
        messages: list[dict[str, str]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "model": configuration.model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "think": False,
            "options": {"temperature": 0},
        }
        for attempt in range(2):
            response = self._request_json(
                configuration.endpoint,
                "POST",
                "/api/chat",
                payload,
            )
            content = response.get("message", {}).get("content")
            try:
                return _parse_provider_content(content)
            except AnswerValidationError:
                if attempt == 1:
                    raise
                payload = {
                    **payload,
                    "messages": [
                        *messages,
                        {
                            "role": "user",
                            "content": (
                                "The previous structured response was invalid. Return "
                                "exactly one JSON object matching required_output_schema, "
                                "with no second object, prose, or code fence."
                            ),
                        },
                    ],
                }
        raise AssertionError("unreachable")

    def _generate_openai_compatible(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        return self._generate_openai_compatible_messages(
            messages=_answer_messages(
                question, normalized_question, evidence, schema=schema
            ),
            configuration=configuration,
            schema=schema,
            schema_name="danish_rag_answer",
        )

    def _generate_openai_compatible_messages(
        self,
        *,
        messages: list[dict[str, str]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
        schema_name: str,
    ) -> dict[str, Any]:
        payload = {
            "model": configuration.model,
            "messages": messages,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
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
        try:
            require_loopback_endpoint(endpoint, purpose="Answer generation")
        except PrivacyBoundaryError as exc:
            raise AnswerPipelineError(str(exc)) from exc
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
        except TimeoutError as exc:
            raise AnswerPipelineError(
                "Local generation provider timed out while preparing the answer. "
                "Keep the question in the composer, confirm the local provider is "
                "running, and retry."
            ) from exc
        except urllib.error.URLError as exc:
            raise AnswerPipelineError(
                "Local generation provider is unavailable. Start the configured local "
                f"provider at {endpoint.rstrip('/')}, confirm the selected model is "
                "loaded, and retry."
            ) from exc
        except json.JSONDecodeError as exc:
            raise AnswerPipelineError(
                "Local generation provider returned malformed JSON before answer "
                "validation. Confirm the configured model supports structured output "
                "and retry."
            ) from exc
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

    def answer(
        self,
        question: str,
        configuration: ProviderConfiguration,
        *,
        conversation_turns: list[dict[str, Any]] | None = None,
    ) -> AnswerResult:
        local_turns = conversation_turns or []
        direct_normalized_question = normalize_question(question)
        conversation_kind = classify_conversation_turn(
            question,
            conversation_turns=local_turns,
        )
        if conversation_kind == "greeting":
            return AnswerResult(
                question=question,
                normalized_question=direct_normalized_question,
                answer=_greeting_answer(),
                model_identity=_model_identity(configuration),
                corpus_identity=str(self.retriever.manifest["corpus_id"]),
            )
        if conversation_kind == "social":
            converse = getattr(self.generator, "converse", None)
            if not callable(converse):
                raise AnswerPipelineError(
                    "Configured generation provider does not support bounded social conversation."
                )
            generated_conversation = converse(
                question=question,
                configuration=configuration,
                schema=conversation_schema(),
            )
            return AnswerResult(
                question=question,
                normalized_question=direct_normalized_question,
                answer=_generated_conversation_answer(generated_conversation),
                model_identity=_model_identity(configuration),
                corpus_identity=str(self.retriever.manifest["corpus_id"]),
            )

        effective_question = _question_with_local_conversation_context(
            question,
            local_turns,
        )
        normalized_question = normalize_question(effective_question)
        ambiguity = classify_question_ambiguity(effective_question)
        if ambiguity.response_kind == "clarification":
            return AnswerResult(
                question=question,
                normalized_question=normalized_question,
                answer=_clarification_answer(ambiguity),
                model_identity=_model_identity(configuration),
                corpus_identity=str(self.retriever.manifest["corpus_id"]),
            )

        evidence = self.retriever.retrieve(effective_question)
        eligible_evidence, blocked_evidence = _partition_evidence_by_policy(evidence)
        safety = classify_question_safety(effective_question)
        if not eligible_evidence:
            answer = _unsupported_answer(
                reason="No approved official evidence was retrieved for this question.",
                blocked_evidence=blocked_evidence,
            )
            return AnswerResult(
                question=question,
                normalized_question=normalized_question,
                answer=answer,
                model_identity=_model_identity(configuration),
                corpus_identity=str(self.retriever.manifest["corpus_id"]),
            )

        if safety.skip_generation:
            generated = _refusal_payload(safety, eligible_evidence)
        else:
            generated = self.generator.generate(
                question=effective_question,
                normalized_question=normalized_question,
                evidence=eligible_evidence,
                configuration=configuration,
                schema=answer_schema(
                    [str(item["citation_id"]) for item in eligible_evidence]
                ),
            )
        generated = _augment_generated_payload(
            generated,
            safety=safety,
            evidence=eligible_evidence,
            blocked_evidence=blocked_evidence,
        )
        _reject_prohibited_safety_claims(generated, safety=safety)
        answer = validate_answer(generated, evidence=eligible_evidence)
        answer["response_kind"] = safety.response_kind
        answer["assumptions"] = ambiguity.assumptions
        answer["suggested_follow_ups"] = _suggested_follow_ups(
            answer=answer,
            evidence=eligible_evidence,
        )
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
    refusal_count = 0
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            raise AnswerValidationError(f"Answer section {index} was not an object.")
        kind = section.get("kind")
        text = section.get("text")
        citation_ids = section.get("citation_ids")
        if kind not in {"official_fact", "interpretation", "refusal", "source_warning"}:
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
        ineligible_citation_ids = [
            citation_id
            for citation_id in normalized_citation_ids
            if not _is_evidence_eligible(evidence_by_citation_id[citation_id])
        ]
        if ineligible_citation_ids:
            raise AnswerValidationError(
                "Answer cited material that is not eligible to support answers: "
                f"{', '.join(sorted(ineligible_citation_ids))}"
            )
        if kind == "official_fact":
            official_fact_count += 1
            if not normalized_citation_ids:
                raise AnswerValidationError(
                    "Answer validation failed: every official fact needs an adjacent citation."
                )
            support = assess_claim_support(
                text,
                (
                    str(evidence_by_citation_id[citation_id].get("content", ""))
                    for citation_id in normalized_citation_ids
                ),
            )
            if not support.supported:
                raise AnswerValidationError(
                    "Answer validation failed: an official fact is not supported by its "
                    "cited evidence."
                )
            cited_official_fact_count += 1
        if kind == "refusal":
            refusal_count += 1
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
    if official_fact_count == 0 and refusal_count == 0:
        raise AnswerValidationError(
            "Answer validation failed: no official fact or evidence-bounded refusal was produced."
        )

    used_evidence = [
        evidence_by_citation_id[citation_id]
        for citation_id in sorted(used_citation_ids)
    ]
    trust = _trust_indicators(
        official_fact_count=official_fact_count,
        cited_official_fact_count=cited_official_fact_count,
        used_evidence=used_evidence,
    )
    return {
        "summary": summary.strip(),
        "response_kind": "answer",
        "assumptions": [],
        "sections": sanitized_sections,
        "citations": _material_sources(sanitized_sections, used_evidence),
        "trust": trust,
    }


def classify_question_safety(question: str) -> SafetyDecision:
    lookup = question.casefold()
    if _asks_for_legal_advice(lookup):
        return SafetyDecision(
            response_kind="refusal",
            refusal_text=(
                "I cannot provide legal advice, legal strategy, or arguments for a "
                "personal case. I can only answer narrower factual questions that are "
                "supported by approved official sources."
            ),
            skip_generation=True,
        )
    if _asks_for_certificate_acceptance(lookup):
        return SafetyDecision(
            response_kind="answer",
            refusal_text=(
                "I cannot decide whether SIRI will accept a personal certificate or "
                "document. I can only explain the documented official equivalence "
                "information from approved sources and point you back to the cited "
                "authority for your individual case."
            ),
        )
    if _asks_for_personal_eligibility(lookup):
        return SafetyDecision(
            response_kind="answer",
            refusal_text=(
                "I cannot decide personal eligibility or recommend an application "
                "choice. The supported facts above are only general official "
                "information; verify your personal case with the cited authority or a "
                "qualified adviser."
            ),
        )
    if _asks_for_permanent_residence_exam_comparison(lookup):
        return SafetyDecision(
            response_kind="answer",
            refusal_text=(
                "I can compare only documented official examination facts and "
                "permanent-residence requirements. I cannot recommend which "
                "examination you should personally take or decide which route fits "
                "your individual case; verify that choice with the cited authority."
            ),
        )
    return SafetyDecision(response_kind="answer")


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
    if has_domain_context and _needs_follow_up_context(question):
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


def classify_question_ambiguity(question: str) -> AmbiguityDecision:
    lookup = question.casefold()
    if _asks_about_source_conflict(lookup):
        return AmbiguityDecision(response_kind="answer", assumptions=[])
    if _is_low_risk_exam_term_question(lookup):
        return AmbiguityDecision(
            response_kind="answer",
            assumptions=[LOW_RISK_EXAM_TERM_ASSUMPTION],
        )
    if _has_consequential_ambiguity(lookup):
        return AmbiguityDecision(
            response_kind="clarification",
            assumptions=[],
            clarification_question=(
                "Which application purpose or exam task are you asking about?"
            ),
            clarification_reason=(
                "The official Danish-test answer can change depending on whether you mean "
                "permanent residence, citizenship, another residence path, or exam "
                "registration logistics."
            ),
        )
    return AmbiguityDecision(response_kind="answer", assumptions=[])


def _question_with_local_conversation_context(
    question: str,
    conversation_turns: list[dict[str, Any]],
) -> str:
    if not conversation_turns:
        return question
    latest_turn = conversation_turns[-1]
    latest_answer = latest_turn.get("answer", {})
    if latest_answer.get("response_kind") != "clarification":
        if not _needs_follow_up_context(question):
            return question
        prior_question = _latest_substantive_question(conversation_turns)
        if not prior_question:
            return question
        return (
            "In the context of the previous question: "
            f"{prior_question}\nFollow-up question: {question.strip()}"
        )
    prior_question = str(latest_turn.get("question", "")).strip()
    if not prior_question:
        return question
    return f"{prior_question}\nClarification: {question.strip()}"


def _needs_follow_up_context(question: str) -> bool:
    compact = " ".join(question.split())
    if not compact:
        return False
    lookup = compact.casefold()
    if "in this context" in lookup or "the cited source" in lookup:
        return True
    contextual_starts = (
        "what about ",
        "how about ",
        "and ",
        "also ",
        "what if ",
        "does that ",
        "does this ",
        "is that ",
        "is this ",
        "can that ",
        "can this ",
        "which one ",
    )
    if lookup.startswith(contextual_starts):
        return True
    words = lookup.split()
    pronouns = {"it", "that", "this", "those", "they", "there"}
    return len(words) <= 8 and bool(pronouns.intersection(words))


def _latest_substantive_question(conversation_turns: list[dict[str, Any]]) -> str:
    for turn in reversed(conversation_turns):
        answer = turn.get("answer", {})
        if answer.get("response_kind") == "clarification":
            continue
        question = str(turn.get("question", "")).strip()
        if question:
            return question
    return ""


def _is_low_risk_exam_term_question(lookup: str) -> bool:
    asks_for_definition = (
        lookup.startswith("what is ")
        or lookup.startswith("what's ")
        or lookup.startswith("what does ")
        or " mean" in lookup
    )
    mentions_exam_term = any(
        term in lookup
        for term in (
            "pd1",
            "pd2",
            "pd3",
            "prøve i dansk",
            "prove i dansk",
            "studieprøven",
            "studieproven",
        )
    )
    asks_for_requirement = any(
        term in lookup for term in (" need", " require", " requirement", " qualify")
    )
    return asks_for_definition and mentions_exam_term and not asks_for_requirement


def _has_consequential_ambiguity(lookup: str) -> bool:
    if _contrasts_permanent_residence_and_citizenship(lookup):
        return True
    if _mixes_registration_and_requirement(lookup):
        return True
    if not _asks_for_danish_test_requirement(lookup):
        return False
    return not _has_specific_application_context(lookup)


def _contrasts_permanent_residence_and_citizenship(lookup: str) -> bool:
    has_permanent_residence = "permanent residence" in lookup or "permanent ophold" in lookup
    has_citizenship = "citizenship" in lookup or "statsborgerskab" in lookup
    contrast_terms = (" or ", " versus ", " vs ", "either", "which one")
    return has_permanent_residence and has_citizenship and any(
        term in lookup for term in contrast_terms
    )


def _mixes_registration_and_requirement(lookup: str) -> bool:
    registration_terms = ("register", "registration", "sign up", "tilmeld")
    requirement_terms = ("requirement", "required", "application")
    return any(term in lookup for term in registration_terms) and any(
        term in lookup for term in requirement_terms
    )


def _asks_for_danish_test_requirement(lookup: str) -> bool:
    test_terms = (
        "danish test",
        "danish exam",
        "danskprøve",
        "dansk prøve",
        "prøve i dansk",
        "prove i dansk",
        "pd1",
        "pd2",
        "pd3",
    )
    requirement_terms = ("need", "required", "requirement", "which", "what")
    return any(term in lookup for term in test_terms) and any(
        term in lookup for term in requirement_terms
    )


def _has_specific_application_context(lookup: str) -> bool:
    context_terms = (
        "permanent residence",
        "permanent ophold",
        "permanent opholdstilladelse",
        "citizenship",
        "statsborgerskab",
        "family reunification",
        "familiesammenføring",
        "register",
        "registration",
        "sign up",
        "tilmeld",
    )
    return any(term in lookup for term in context_terms)


def _asks_about_source_conflict(lookup: str) -> bool:
    source_terms = ("official page", "official source", "approved source", "source says")
    conflict_terms = ("another", "different", "conflict", "conflicting", "which one is right")
    return any(term in lookup for term in source_terms) and any(
        term in lookup for term in conflict_terms
    )


def _asks_for_legal_advice(lookup: str) -> bool:
    legal_advice_terms = (
        "legal advice",
        "legal strategy",
        "how to argue",
        "argue that",
        "lawyer",
        "appeal argument",
        "represent me",
    )
    return any(term in lookup for term in legal_advice_terms)


def _asks_for_personal_eligibility(lookup: str) -> bool:
    eligibility_terms = (
        "do i qualify",
        "do i personally qualify",
        "am i eligible",
        "do i meet",
        "can i get permanent residence",
        "will i get permanent residence",
        "should i apply",
        "recommend whether i should apply",
    )
    return any(term in lookup for term in eligibility_terms)


def _asks_for_permanent_residence_exam_comparison(lookup: str) -> bool:
    comparison_terms = ("compare", "difference between", "differences between")
    residence_terms = (
        "permanent residence",
        "permanent ophold",
        "permanent opholdstilladelse",
    )
    exam_terms = (
        "prøve i dansk",
        "prove i dansk",
        "studieprøven",
        "studieproeven",
    )
    return all(
        (
            any(term in lookup for term in comparison_terms),
            any(term in lookup for term in residence_terms),
            sum(term in lookup for term in exam_terms) >= 2,
        )
    )


def _asks_for_certificate_acceptance(lookup: str) -> bool:
    certificate_terms = (
        "certificate",
        "diploma",
        "old danish",
        "documentation",
        "bevis",
    )
    acceptance_terms = (
        "will accept",
        "will be accepted",
        "whether siri will accept",
        "count for me",
        "valid for me",
        "accepted for permanent residence",
    )
    return any(term in lookup for term in certificate_terms) and any(
        term in lookup for term in acceptance_terms
    )


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


def _clarification_answer(decision: AmbiguityDecision) -> dict[str, Any]:
    question = decision.clarification_question
    reason = decision.clarification_reason
    return {
        "summary": f"{question} I need to clarify before giving a Danish-test requirement.",
        "response_kind": "clarification",
        "assumptions": [],
        "sections": [
            {
                "kind": "clarification",
                "label": "Clarification needed",
                "text": f"{question} {reason}",
                "citation_ids": [],
                "citations": [],
            }
        ],
        "citations": [],
        "trust": {
            "evidence_confidence": "Low",
            "evidence_confidence_reason": (
                "No substantive answer was generated because the missing context can "
                "change the official answer."
            ),
            "fresh_tomato_score": "Low",
            "fresh_tomato_reason": (
                "No material source has been attached to this clarification turn."
            ),
        },
    }


def _unsupported_answer(
    *,
    reason: str,
    blocked_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    sections = [
        {
            "kind": "refusal",
            "label": "Evidence-bounded refusal",
            "text": (
                f"{reason} I will not substitute a generation-model fact for missing "
                "approved official evidence. Ask a narrower question or install an "
                "approved knowledge release that contains the required source."
            ),
            "citation_ids": [],
            "citations": [],
        }
    ]
    if blocked_evidence:
        sections.append(_blocked_source_warning_section(blocked_evidence))
    return {
        "summary": reason,
        "response_kind": "refusal",
        "assumptions": [],
        "sections": sections,
        "citations": [],
        "trust": {
            "evidence_confidence": "Low",
            "evidence_confidence_reason": (
                "No eligible approved official material source could support a "
                "substantive answer."
            ),
            "fresh_tomato_score": "Low",
            "fresh_tomato_reason": (
                "Source freshness is low because no current healthy material source "
                "could be attached."
            ),
        },
    }


def _refusal_payload(
    decision: SafetyDecision,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    citation_ids = [
        str(item["citation_id"])
        for item in evidence
        if "safety-boundary" in item.get("topic_tags", [])
        or "evidence-boundary" in item.get("topic_tags", [])
    ]
    return {
        "summary": "I need to refuse this part of the request.",
        "sections": [
            {
                "kind": "refusal",
                "text": decision.refusal_text,
                "citation_ids": citation_ids,
            }
        ],
    }


def _augment_generated_payload(
    payload: dict[str, Any],
    *,
    safety: SafetyDecision,
    evidence: list[dict[str, Any]],
    blocked_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    augmented = dict(payload)
    sections = [dict(section) for section in augmented.get("sections", [])]
    if safety.refusal_text:
        augmented["summary"] = (
            "This answer separates supported official facts from the personal or "
            "legal decision I cannot make."
        )
        if not _has_refusal_section(sections, safety.refusal_text):
            sections.append(
                {
                    "kind": "refusal",
                    "text": safety.refusal_text,
                    "citation_ids": [],
                }
            )
    conflict_warning = _conflict_warning_section(evidence)
    if conflict_warning:
        sections.append(conflict_warning)
    stale_warning = _stale_warning_section(evidence)
    if stale_warning:
        sections.append(stale_warning)
    if blocked_evidence:
        sections.append(_blocked_source_warning_section(blocked_evidence))
    augmented["sections"] = sections
    return augmented


def _has_refusal_section(sections: list[dict[str, Any]], refusal_text: str) -> bool:
    refusal_lookup = refusal_text.casefold()
    for section in sections:
        if section.get("kind") != "refusal":
            continue
        if refusal_lookup in str(section.get("text", "")).casefold():
            return True
    return False


def _conflict_warning_section(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not any(_is_conflicting_evidence(item) for item in evidence):
        return None
    return {
        "kind": "source_warning",
        "text": (
            "Retrieved approved official sources include a conflict. This answer must "
            "not silently choose which source is right; verify the current official "
            "authority before relying on the disputed point."
        ),
        "citation_ids": sorted(str(item["citation_id"]) for item in evidence),
    }


def _stale_warning_section(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    stale_ids = sorted(
        str(item["citation_id"])
        for item in evidence
        if item.get("source_health") == "overdue-policy-usable"
    )
    if not stale_ids:
        return None
    return {
        "kind": "source_warning",
        "text": (
            "At least one material source is policy-usable but overdue for review. "
            "Use the supported fact only within the cited source's scope and check "
            "the official page before relying on current logistics or deadlines."
        ),
        "citation_ids": stale_ids,
    }


def _blocked_source_warning_section(
    blocked_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    blocked_descriptions = [
        (
            f"{item.get('citation_id', item.get('document_id', '<unknown>'))} "
            f"({item.get('review_state', '<unknown>')}/"
            f"{item.get('source_health', '<unknown>')}/"
            f"{item.get('approval_state', 'approved')})"
        )
        for item in blocked_evidence
    ]
    return {
        "kind": "source_warning",
        "label": "Source warning",
        "text": (
            "Some retrieved source material was blocked by source policy and was not "
            "sent as answer-supporting evidence: "
            f"{', '.join(sorted(blocked_descriptions))}."
        ),
        "citation_ids": [],
        "citations": [],
    }


def _partition_evidence_by_policy(
    evidence: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for item in evidence:
        if _is_evidence_eligible(item):
            eligible.append(item)
        else:
            blocked.append(item)
    return eligible, blocked


def _is_evidence_eligible(evidence: dict[str, Any]) -> bool:
    return assess_source_freshness(evidence).answer_eligible


def _reject_prohibited_safety_claims(
    payload: dict[str, Any],
    *,
    safety: SafetyDecision,
) -> None:
    if not safety.refusal_text:
        return
    answer_text = " ".join(
        [
            str(payload.get("summary", "")),
            *[
                str(section.get("text", ""))
                for section in payload.get("sections", [])
                if section.get("kind") != "refusal"
            ],
        ]
    ).casefold()
    prohibited_phrases = [
        "you qualify",
        "you do qualify",
        "you are eligible",
        "you should apply",
        "you do not qualify",
        "you are not eligible",
        "siri will accept",
        "will accept your certificate",
        "your certificate will be accepted",
        "your diploma will be accepted",
        "you should argue",
        "your legal strategy",
    ]
    matched = [phrase for phrase in prohibited_phrases if phrase in answer_text]
    if matched:
        raise AnswerValidationError(
            "Answer validation failed: safety-sensitive request produced a prohibited "
            f"personal or legal conclusion ({', '.join(sorted(matched))})."
        )


def _answer_messages(
    question: str,
    normalized_question: str,
    evidence: list[dict[str, Any]],
    *,
    schema: dict[str, Any] | None = None,
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
    user_payload: dict[str, Any] = {
        "question": question,
        "normalized_question": normalized_question,
        "approved_official_evidence": evidence_payload,
    }
    if schema is not None:
        user_payload["required_output_schema"] = schema
    return [
        {
            "role": "system",
            "content": (
                "Return only JSON matching the provided schema. Answer in English and "
                "preserve important Danish terms. Use citation_ids exactly as provided in "
                "approved_official_evidence; never invent or alter an ID. Cite every "
                "official_fact. Keep each official_fact to one factual proposition and a "
                "close paraphrase of its cited evidence. If the evidence directly supports "
                "any part of the question, answer every part that the evidence directly "
                "supports. Use a refusal section with no citations only for a requested "
                "detail that the evidence does not support. Never claim that something is "
                "absent, invalid, or disallowed unless the evidence explicitly says so. "
                "The summary may summarize sections but must not add official facts. "
                "The exact required_output_schema is included in the user payload."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                user_payload,
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    ]


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


def _suggested_follow_ups(
    *,
    answer: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> list[str]:
    if not answer.get("citations"):
        return []

    material_citation_ids = {
        str(citation["citation_id"])
        for citation in answer.get("citations", [])
    }
    material_evidence = [
        item
        for item in evidence
        if str(item.get("citation_id")) in material_citation_ids
    ]
    if not material_evidence:
        return []

    evidence_text = " ".join(
        [
            *[str(item.get("title", "")) for item in material_evidence],
            *[str(item.get("content", "")) for item in material_evidence],
            *[
                " ".join(str(tag) for tag in item.get("topic_tags", []))
                for item in material_evidence
            ],
        ]
    ).casefold()
    suggestions = ["Which cited source supports this answer?"]
    if "prøve i dansk 2" in evidence_text or "prove i dansk 2" in evidence_text:
        suggestions.insert(0, "What does Prøve i Dansk 2 mean in this context?")
    if "equivalent" in evidence_text or "tilsvarende" in evidence_text:
        suggestions.append("What does the cited source say about equivalent Danish tests?")
    if "registration" in evidence_text or "tilmeld" in evidence_text:
        suggestions.append("What does the cited source say about exam registration?")
    return _safe_suggested_follow_ups(suggestions)


def _safe_suggested_follow_ups(suggestions: list[str]) -> list[str]:
    unsafe_phrases = (
        "do i qualify",
        "am i eligible",
        "should i apply",
        "recommend",
        "legal advice",
        "legal strategy",
    )
    safe: list[str] = []
    for suggestion in suggestions:
        compact = " ".join(suggestion.split()).strip()
        if not compact:
            continue
        lookup = compact.casefold()
        if any(phrase in lookup for phrase in unsafe_phrases):
            continue
        if compact not in safe:
            safe.append(compact)
    return safe[:3]


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
    if official_fact_count == 0:
        evidence_confidence = TrustLevel.LOW
        evidence_reason = (
            "No official fact was produced; the response is limited to an "
            "evidence-bounded refusal or source warning."
        )
    elif coverage_complete and not conflicting_sources:
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
    assessment = assess_source_freshness(evidence)
    return TrustLevel(assessment.level), assessment.reason


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
        "source_warning": "Source warning",
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
