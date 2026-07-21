"""Conservative, deterministic claim-to-citation support checks.

This validator is a release guard, not a semantic truth oracle.  It rejects claims
whose material terms or numeric facts are absent from the cited approved evidence.
The final-answer evaluation still independently measures citation correctness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


TOKEN_PATTERN = re.compile(r"[0-9a-zA-ZæøåÆØÅ]+")
NUMBER_PATTERN = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?(?![\w])")

_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "by",
    "can",
    "could",
    "for",
    "from",
    "has",
    "have",
    "having",
    "i",
    "if",
    "in",
    "is",
    "it",
    "its",
    "may",
    "must",
    "of",
    "on",
    "or",
    "should",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "were",
    "with",
    "you",
    "your",
    "af",
    "at",
    "den",
    "det",
    "der",
    "en",
    "er",
    "et",
    "eller",
    "for",
    "fra",
    "har",
    "i",
    "kan",
    "med",
    "og",
    "om",
    "på",
    "som",
    "til",
}

_TOKEN_EQUIVALENTS = {
    "applicants": "applicant",
    "application": "applicant",
    "applications": "applicant",
    "bestået": "pass",
    "består": "pass",
    "cost": "fee",
    "costs": "fee",
    "deadline": "deadline",
    "deadlines": "deadline",
    "documents": "documentation",
    "documented": "documentation",
    "danskprøve": "test",
    "danskprøver": "test",
    "exams": "exam",
    "fees": "fee",
    "higher": "higher",
    "kræve": "require",
    "kræver": "require",
    "qualifies": "qualify",
    "qualified": "qualify",
    "requirements": "require",
    "requirement": "require",
    "required": "require",
    "requires": "require",
    "requiring": "require",
    "registration": "register",
    "registered": "register",
    "registering": "register",
    "satisfied": "satisfy",
    "satisfies": "satisfy",
    "satisfying": "satisfy",
    "tests": "test",
    "tilsvarende": "equivalent",
}

_PHRASE_EQUIVALENTS = {
    "pd1": "prøve i dansk 1",
    "pd2": "prøve i dansk 2",
    "pd3": "prøve i dansk 3",
    "studieproven": "studieprøven",
    "permanent residence": "permanent opholdstilladelse",
}


@dataclass(frozen=True)
class ClaimSupportResult:
    supported: bool
    matched_term_ratio: float
    missing_terms: tuple[str, ...]
    missing_numbers: tuple[str, ...]


def assess_claim_support(
    claim: str,
    evidence_texts: Iterable[str],
    *,
    minimum_term_ratio: float = 0.70,
) -> ClaimSupportResult:
    """Check whether a factual claim is materially supported by cited text.

    Numeric facts are hard constraints.  The lexical threshold is intentionally
    conservative so an on-topic but irrelevant citation does not receive credit.
    """

    normalized_claim = _normalize_phrases(claim)
    joined_evidence = " ".join(_normalize_phrases(text) for text in evidence_texts)
    claim_numbers = set(NUMBER_PATTERN.findall(normalized_claim))
    evidence_numbers = set(NUMBER_PATTERN.findall(joined_evidence))
    missing_numbers = tuple(sorted(claim_numbers - evidence_numbers))

    claim_terms = _material_terms(normalized_claim)
    evidence_terms = _material_terms(joined_evidence)
    matched_terms = claim_terms & evidence_terms
    ratio = len(matched_terms) / len(claim_terms) if claim_terms else 0.0
    missing_terms = tuple(sorted(claim_terms - evidence_terms))
    supported = (
        bool(claim_terms)
        and not missing_numbers
        and ratio >= minimum_term_ratio
    )
    return ClaimSupportResult(
        supported=supported,
        matched_term_ratio=ratio,
        missing_terms=missing_terms,
        missing_numbers=missing_numbers,
    )


def _normalize_phrases(text: str) -> str:
    normalized = text.casefold()
    for phrase, replacement in _PHRASE_EQUIVALENTS.items():
        normalized = re.sub(rf"\b{re.escape(phrase)}\b", replacement, normalized)
    return normalized


def _material_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw_token in TOKEN_PATTERN.findall(text.casefold()):
        if raw_token in _STOP_WORDS:
            continue
        token = _TOKEN_EQUIVALENTS.get(raw_token, raw_token)
        if token.isdigit() or len(token) >= 2:
            terms.add(token)
    return terms
