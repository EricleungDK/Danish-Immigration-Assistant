"""Supported corpus shapes and their derived retrieval-index identities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CorpusSchemaContract:
    version: str
    content_unit_schema_version: str | None
    index_schema_version: str
    indexed_unit: str


WHOLE_DOCUMENT_CORPUS_SCHEMA = CorpusSchemaContract(
    version="1.0",
    content_unit_schema_version=None,
    index_schema_version="hybrid-index-v1",
    indexed_unit="whole-document",
)
SEMANTIC_CHUNK_CORPUS_SCHEMA = CorpusSchemaContract(
    version="2.0",
    content_unit_schema_version="semantic-chunk-v1",
    index_schema_version="hybrid-chunk-index-v1",
    indexed_unit="semantic-chunk",
)
SEMANTIC_CHUNK_DOCUMENT_FIELDS = frozenset(
    {
        "chunk_id",
        "source_document_id",
        "chunk_index",
        "chunk_content_sha256",
    }
)
SUPPORTED_CORPUS_SCHEMAS = {
    contract.version: contract
    for contract in (
        WHOLE_DOCUMENT_CORPUS_SCHEMA,
        SEMANTIC_CHUNK_CORPUS_SCHEMA,
    )
}


def corpus_schema_contract(version: str) -> CorpusSchemaContract | None:
    return SUPPORTED_CORPUS_SCHEMAS.get(version)
