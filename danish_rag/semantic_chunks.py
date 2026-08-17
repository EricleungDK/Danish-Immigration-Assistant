"""Deterministic semantic chunks for reviewed normalized source documents."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any


DEFAULT_MAX_CHUNK_CHARACTERS = 1_200
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


class SemanticChunkError(ValueError):
    """Raised when reviewed source material cannot produce trusted chunks."""


def build_stable_semantic_chunks(
    *,
    source: dict[str, Any],
    document: dict[str, Any],
    max_chunk_characters: int = DEFAULT_MAX_CHUNK_CHARACTERS,
) -> list[dict[str, Any]]:
    """Divide one reviewed normalized document into stable retrieval chunks.

    Chunk identifiers are derived from the approved source ID, the normalized
    chunk content, and the occurrence of identical content within that source.
    Display metadata and chunk ordering therefore do not silently redefine a
    chunk's identity.
    """

    source_id = str(source.get("source_id", "")).strip()
    document_source_id = str(document.get("source_id", "")).strip()
    if not source_id or document_source_id != source_id:
        raise SemanticChunkError(
            "A chunked document must reference the reviewed source being chunked."
        )
    if source.get("review_state") not in {
        "approved-current",
        "overdue-policy-usable",
    }:
        raise SemanticChunkError("Only a reviewed release-eligible source can be chunked.")
    if (
        document.get("review_state")
        not in {"approved-current", "overdue-policy-usable"}
        or document.get("approval_state") != "approved"
    ):
        raise SemanticChunkError(
            "Only an approved normalized document can be divided into chunks."
        )
    normalized_document_sha256 = str(
        source.get("normalized_document_sha256", "")
    ).casefold()
    if not _SHA256_PATTERN.fullmatch(normalized_document_sha256):
        raise SemanticChunkError(
            "Reviewed source is missing a valid normalized document content identity."
        )
    document_content = str(document.get("content", ""))
    normalized_content = " ".join(document_content.split())
    if _sha256_text(normalized_content) != normalized_document_sha256:
        raise SemanticChunkError(
            "Normalized document content does not match the reviewed normalized "
            "content identity."
        )
    if max_chunk_characters < 1:
        raise SemanticChunkError("Maximum chunk size must be a positive integer.")

    semantic_units = _semantic_units(
        document_content,
        max_chunk_characters=max_chunk_characters,
    )
    if not semantic_units:
        raise SemanticChunkError("Reviewed normalized source content is empty.")

    source_document_identity = document.get("document_id")
    if (
        not isinstance(source_document_identity, str)
        or not source_document_identity.strip()
    ):
        raise SemanticChunkError("Reviewed normalized document is missing its identity.")
    source_document_id = source_document_identity.strip()

    occurrences: defaultdict[str, int] = defaultdict(int)
    chunks: list[dict[str, Any]] = []
    for chunk_index, content in enumerate(semantic_units):
        chunk_content_sha256 = _sha256_text(content)
        occurrence = occurrences[chunk_content_sha256]
        occurrences[chunk_content_sha256] += 1
        chunk_id = stable_chunk_id(
            source_id=source_id,
            source_document_id=source_document_id,
            chunk_content_sha256=chunk_content_sha256,
            occurrence=occurrence,
        )
        chunks.append(
            {
                **document,
                "document_id": chunk_id,
                "chunk_id": chunk_id,
                "source_document_id": source_document_id,
                "chunk_index": chunk_index,
                "chunk_content_sha256": chunk_content_sha256,
                "normalized_document_sha256": normalized_document_sha256,
                "content": content,
            }
        )
    return chunks


def _semantic_units(
    content: str,
    *,
    max_chunk_characters: int,
) -> list[str]:
    paragraphs = [
        " ".join(paragraph.split())
        for paragraph in re.split(r"\n\s*\n", content.replace("\r\n", "\n"))
        if paragraph.strip()
    ]
    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chunk_characters:
            units.append(paragraph)
            continue
        units.extend(
            _split_oversized_paragraph(
                paragraph,
                max_chunk_characters=max_chunk_characters,
            )
        )
    return units


def _split_oversized_paragraph(
    paragraph: str,
    *,
    max_chunk_characters: int,
) -> list[str]:
    sentences = _SENTENCE_BOUNDARY.split(paragraph)
    chunks: list[str] = []
    pending = ""
    for sentence in sentences:
        for segment in _hard_wrap(sentence, max_chunk_characters):
            candidate = f"{pending} {segment}".strip()
            if pending and len(candidate) > max_chunk_characters:
                chunks.append(pending)
                pending = segment
            else:
                pending = candidate
    if pending:
        chunks.append(pending)
    return chunks


def _hard_wrap(text: str, max_characters: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    segments: list[str] = []
    pending = ""
    for word in words:
        if len(word) > max_characters:
            if pending:
                segments.append(pending)
                pending = ""
            segments.extend(
                word[index : index + max_characters]
                for index in range(0, len(word), max_characters)
            )
            continue
        candidate = f"{pending} {word}".strip()
        if pending and len(candidate) > max_characters:
            segments.append(pending)
            pending = word
        else:
            pending = candidate
    if pending:
        segments.append(pending)
    return segments


def stable_chunk_id(
    *,
    source_id: str,
    source_document_id: str,
    chunk_content_sha256: str,
    occurrence: int,
) -> str:
    identity = (
        f"{source_id}\0{source_document_id}\0{chunk_content_sha256}\0{occurrence}"
    )
    digest = _sha256_text(identity)
    return f"{source_id}::chunk::{digest[:24]}"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
