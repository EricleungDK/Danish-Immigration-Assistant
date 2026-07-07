"""Application retrieval for locally installed approved official sources."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

from .knowledge_release import load_active_documents, load_active_release


INDEX_SCHEMA_VERSION = "hybrid-index-v1"
DENSE_ENGINE = "local-dense-json"
LEXICAL_ENGINE = "sqlite-fts5"
SUPPORTED_EMBEDDING_MODEL = "embeddinggemma"
VECTOR_DIMENSIONS = 64
RRF_K = 60
TOKEN_PATTERN = re.compile(r"[0-9a-zA-ZæøåÆØÅ]+")
ELIGIBLE_REVIEW_STATES = {"approved-current", "overdue-policy-usable"}
ELIGIBLE_SOURCE_HEALTH = {"healthy", "overdue-policy-usable"}


class RetrievalError(ValueError):
    """Raised when the local retrieval index is missing or incompatible."""


SUPPORTED_EMBEDDING_MODELS: dict[str, dict[str, Any]] = {
    "embeddinggemma": {
        "name": "embeddinggemma",
        "implementation": "deterministic-local-vector-fixture",
        "capabilities": ["embedding"],
        "vector_dimensions": VECTOR_DIMENSIONS,
    },
    "embeddinggemma:latest": {
        "name": "embeddinggemma:latest",
        "implementation": "deterministic-local-vector-fixture",
        "capabilities": ["embedding"],
        "vector_dimensions": VECTOR_DIMENSIONS,
    },
}


class UnsupportedEmbeddingModelError(RetrievalError):
    """Raised when a requested embedding model is not approved for local indexing."""


def supported_embedding_models() -> list[dict[str, Any]]:
    return [
        {
            "name": profile["name"],
            "vector_dimensions": int(profile["vector_dimensions"]),
            "implementation": str(profile["implementation"]),
            "capabilities": list(profile["capabilities"]),
        }
        for profile in SUPPORTED_EMBEDDING_MODELS.values()
    ]


def embedding_model_profile(embedding_model: str | None = None) -> dict[str, Any]:
    requested = (embedding_model or SUPPORTED_EMBEDDING_MODEL).strip()
    profile = SUPPORTED_EMBEDDING_MODELS.get(requested)
    if profile is None:
        supported = ", ".join(sorted(SUPPORTED_EMBEDDING_MODELS))
        raise UnsupportedEmbeddingModelError(
            f"Unsupported embedding model '{requested}'. Choose a supported local "
            f"embedding model before indexing: {supported}. Changing embedding model "
            "requires rebuilding the local hybrid index for the active corpus."
        )
    if "embedding" not in profile.get("capabilities", []):
        raise UnsupportedEmbeddingModelError(
            f"Model '{requested}' is supported by name but lacks the embedding "
            "capability required to build the local hybrid index."
        )
    return dict(profile)


def normalize_question(question: str) -> str:
    normalized = question.strip()
    lookup = normalized.casefold()
    expansions: list[str] = []
    phrase_expansions = {
        "permanent residence": "permanent ophold permanent opholdstilladelse",
        "residence permit": "opholdstilladelse",
        "danish test": "Prøve i Dansk danskprøve dansk prøve",
        "danish exam": "Prøve i Dansk danskprøve dansk prøve",
        "language requirement": "sprogkrav danskkrav danskprøve",
        "language requirements": "sprogkrav danskkrav danskprøve",
        "compare": "sammenlign forskel exam-comparison Prøve i Dansk Studieprøven",
        "registration": "tilmelding tilmeldingsfrist prøvedatoer sprogcenter",
        "register": "tilmelding tilmeldingsfrist prøvedatoer sprogcenter",
        "sign up": "tilmelding tilmeldingsfrist prøvedatoer sprogcenter",
        "certificate": "equivalence equivalent higher level diploma bevis dokumentation",
        "accepted": "accept certificate equivalence equivalent higher level",
        "passed": "bestået",
        "pass": "bestået",
        "need": "kræve kræver krav",
        "pd1": "Prøve i Dansk 1",
        "pd2": "Prøve i Dansk 2",
        "pd3": "Prøve i Dansk 3",
        "studieproven": "Studieprøven",
    }
    for phrase, expansion in phrase_expansions.items():
        if phrase in lookup:
            expansions.append(expansion)
    if "danish" in lookup and "test" not in lookup and "exam" not in lookup:
        expansions.append("dansk Prøve i Dansk danskprøve")
    if "permanent" in lookup and "permanent residence" not in lookup:
        expansions.append("permanent ophold permanent opholdstilladelse")
    return " ".join([normalized, *expansions]).strip()


def build_hybrid_index(
    data_dir: str | Path,
    documents: list[dict[str, Any]],
    *,
    manifest: dict[str, Any],
    embedding_model: str | None = None,
    progress_callback: Any | None = None,
    fault_injector: Any | None = None,
) -> dict[str, Any]:
    embedding_profile = embedding_model_profile(embedding_model)
    release_id = str(manifest["knowledge_release_id"])
    index_dir = Path(data_dir) / "index" / release_id
    index_dir.mkdir(parents=True, exist_ok=True)
    _report_install_phase(
        progress_callback,
        "indexing",
        "Building lexical retrieval index.",
        45,
    )
    _inject_install_fault(fault_injector, "indexing")

    lexical_path = index_dir / "lexical.sqlite3"
    if lexical_path.exists():
        lexical_path.unlink()
    connection = sqlite3.connect(lexical_path)
    try:
        _create_lexical_schema(connection)
        for document in documents:
            connection.execute(
                """
                INSERT INTO documents (
                    document_id,
                    source_id,
                    title,
                    publisher,
                    official_url,
                    language,
                    topic_tags,
                    review_state,
                    source_health,
                    checked_at_utc,
                    content,
                    document_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document["document_id"],
                    document["source_id"],
                    document["title"],
                    document["publisher"],
                    document["official_url"],
                    document["language"],
                    json.dumps(document["topic_tags"], sort_keys=True),
                    document["review_state"],
                    document["source_health"],
                    document["checked_at_utc"],
                    document["content"],
                    json.dumps(document, sort_keys=True),
                ),
            )
            connection.execute(
                "INSERT INTO documents_fts(document_id, content) VALUES (?, ?)",
                (document["document_id"], _search_text(document)),
            )
        connection.commit()
    finally:
        connection.close()

    _report_install_phase(
        progress_callback,
        "embedding",
        "Embedding release documents locally.",
        70,
    )
    _inject_install_fault(fault_injector, "embedding")
    vectors = [
        {
            "document_id": document["document_id"],
            "vector": embed_text(
                _search_text(document),
                dimensions=int(embedding_profile["vector_dimensions"]),
            ),
        }
        for document in documents
        if _is_release_eligible(document)
    ]
    dense_index = {
        "metadata": _index_metadata(manifest, embedding_profile=embedding_profile),
        "vectors": vectors,
    }
    _write_json(index_dir / "dense-index.json", dense_index)
    metadata = _index_metadata(manifest, embedding_profile=embedding_profile)
    _write_json(index_dir / "index-metadata.json", metadata)
    return metadata


class HybridRetriever:
    def __init__(
        self,
        *,
        data_dir: str | Path,
        active_release: dict[str, Any],
        documents: list[dict[str, Any]],
        dense_index: dict[str, Any],
        embedding_model: str | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.active_release = active_release
        self.manifest = active_release["manifest"]
        self.documents_by_id = {document["document_id"]: document for document in documents}
        self.dense_index = dense_index
        self.index_dir = self.data_dir / "index" / self.manifest["knowledge_release_id"]
        self.embedding_profile = embedding_model_profile(
            embedding_model or dense_index.get("metadata", {}).get("embedding_model")
        )
        self._validate_index()

    @classmethod
    def from_data_dir(cls, data_dir: str | Path) -> "HybridRetriever":
        active_release = load_active_release(data_dir)
        documents = load_active_documents(data_dir)
        release_id = active_release["manifest"]["knowledge_release_id"]
        dense_index = json.loads(
            (Path(data_dir) / "index" / release_id / "dense-index.json").read_text(
                encoding="utf-8"
            )
        )
        return cls(
            data_dir=data_dir,
            active_release=active_release,
            documents=documents,
            dense_index=dense_index,
        )

    def retrieve(self, question: str, *, limit: int = 3) -> list[dict[str, Any]]:
        normalized_question = normalize_question(question)
        metadata_filter = _metadata_filter_for_question(normalized_question)
        lexical_ids = self._lexical_ranked_ids(normalized_question, metadata_filter)
        dense_ids = self._dense_ranked_ids(normalized_question, metadata_filter)
        fused_ids, fusion_scores = reciprocal_rank_fusion([lexical_ids, dense_ids], k=RRF_K)
        results: list[dict[str, Any]] = []
        for document_id in fused_ids:
            document = self.documents_by_id[document_id]
            if not _is_release_eligible(document):
                continue
            if not _matches_metadata_filter(document, metadata_filter):
                continue
            result = dict(document)
            result["citation_id"] = document["document_id"]
            result["corpus_identity"] = self.manifest["corpus_id"]
            result["knowledge_release_id"] = self.manifest["knowledge_release_id"]
            result["retrieval_score"] = fusion_scores.get(document_id, 0.0)
            results.append(result)
            if len(results) >= limit:
                break
        return results

    def _lexical_ranked_ids(
        self,
        normalized_question: str,
        metadata_filter: dict[str, Any],
    ) -> list[str]:
        expression = _fts_match_expression(normalized_question)
        if not expression:
            return []
        connection = sqlite3.connect(self.index_dir / "lexical.sqlite3")
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT documents.document_id, bm25(documents_fts) AS rank
                FROM documents_fts
                JOIN documents ON documents.document_id = documents_fts.document_id
                WHERE documents_fts MATCH ?
                ORDER BY rank
                LIMIT 20
                """,
                (expression,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            connection.close()
        ranked_ids = [str(row["document_id"]) for row in rows]
        return [
            document_id
            for document_id in ranked_ids
            if _is_release_eligible(self.documents_by_id[document_id])
            and _matches_metadata_filter(self.documents_by_id[document_id], metadata_filter)
        ]

    def _dense_ranked_ids(
        self,
        normalized_question: str,
        metadata_filter: dict[str, Any],
    ) -> list[str]:
        query_vector = embed_text(
            normalized_question,
            dimensions=int(self.embedding_profile["vector_dimensions"]),
        )
        scored: list[tuple[str, float]] = []
        for item in self.dense_index["vectors"]:
            document_id = item["document_id"]
            document = self.documents_by_id[document_id]
            if not _is_release_eligible(document):
                continue
            if not _matches_metadata_filter(document, metadata_filter):
                continue
            scored.append((document_id, cosine_similarity(query_vector, item["vector"])))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return [document_id for document_id, _score in scored[:20]]

    def _validate_index(self) -> None:
        metadata = self.dense_index.get("metadata", {})
        expected = _index_metadata(self.manifest, embedding_profile=self.embedding_profile)
        mismatches = [
            field for field, value in expected.items() if metadata.get(field) != value
        ]
        if mismatches:
            raise RetrievalError(
                "Local dense index is incompatible; re-index required. "
                f"Mismatched field(s): {', '.join(mismatches)}"
            )
        lexical_path = self.index_dir / "lexical.sqlite3"
        if not lexical_path.exists():
            raise RetrievalError("Local lexical index is missing; re-index required.")


def embed_text(text: str, *, dimensions: int = VECTOR_DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    for token in TOKEN_PATTERN.findall(text.casefold()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [round(value / norm, 9) for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    *,
    k: int = RRF_K,
) -> tuple[list[str], dict[str, float]]:
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    first_source: dict[str, int] = {}
    for source_index, ranking in enumerate(rankings):
        seen: set[str] = set()
        for rank, result_id in enumerate(ranking, start=1):
            if result_id in seen:
                continue
            seen.add(result_id)
            scores[result_id] = scores.get(result_id, 0.0) + (1.0 / (k + rank))
            best_rank[result_id] = min(best_rank.get(result_id, rank), rank)
            first_source.setdefault(result_id, source_index)
    ranked = sorted(
        scores,
        key=lambda result_id: (
            -scores[result_id],
            best_rank[result_id],
            first_source[result_id],
            result_id,
        ),
    )
    return ranked, {result_id: round(scores[result_id], 9) for result_id in ranked}


def _create_lexical_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE documents (
            document_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            title TEXT NOT NULL,
            publisher TEXT NOT NULL,
            official_url TEXT NOT NULL,
            language TEXT NOT NULL,
            topic_tags TEXT NOT NULL,
            review_state TEXT NOT NULL,
            source_health TEXT NOT NULL,
            checked_at_utc TEXT NOT NULL,
            content TEXT NOT NULL,
            document_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE VIRTUAL TABLE documents_fts
        USING fts5(document_id UNINDEXED, content, tokenize = 'unicode61')
        """
    )


def _search_text(document: dict[str, Any]) -> str:
    return " ".join(
        [
            str(document.get("title", "")),
            str(document.get("publisher", "")),
            " ".join(str(tag) for tag in document.get("topic_tags", [])),
            str(document.get("content", "")),
            str(document.get("english_search_terms", "")),
        ]
    )


def _index_metadata(
    manifest: dict[str, Any],
    *,
    embedding_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = embedding_model_profile(
        str(embedding_profile["name"]) if embedding_profile else SUPPORTED_EMBEDDING_MODEL
    )
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "retrieval": "hybrid",
        "lexical_engine": LEXICAL_ENGINE,
        "dense_engine": DENSE_ENGINE,
        "embedding_model": profile["name"],
        "embedding_model_identity": {
            "name": profile["name"],
            "implementation": profile["implementation"],
            "capabilities": list(profile["capabilities"]),
        },
        "vector_dimensions": int(profile["vector_dimensions"]),
        "corpus_identity": manifest["corpus_id"],
        "knowledge_release_id": manifest["knowledge_release_id"],
        "rrf_k": RRF_K,
    }


def _is_release_eligible(document: dict[str, Any]) -> bool:
    return (
        document.get("review_state") in ELIGIBLE_REVIEW_STATES
        and document.get("source_health") in ELIGIBLE_SOURCE_HEALTH
        and document.get("approval_state", "approved") == "approved"
    )


def _metadata_filter_for_question(normalized_question: str) -> dict[str, Any]:
    lookup = normalized_question.casefold()
    topic_tags: list[str] = []
    is_exam_comparison = any(
        term in lookup for term in ("compare", "sammenlign", "exam-comparison")
    )
    if ("permanent" in lookup or "ophold" in lookup) and not is_exam_comparison:
        topic_tags.append("permanent-residence")
    if (
        "language" in lookup
        or "sprog" in lookup
        or "dansk" in lookup
        or "prøve" in lookup
        or "test" in lookup
        or "exam" in lookup
    ):
        topic_tags.append("language-requirement")
    if is_exam_comparison:
        topic_tags.append("exam-comparison")
    if any(
        term in lookup
        for term in ("register", "registration", "sign up", "tilmeld", "tilmelding")
    ):
        topic_tags.append("registration-logistics")
    if any(
        term in lookup
        for term in ("certificate", "diploma", "bevis", "equivalent", "equivalence")
    ):
        topic_tags.append("certificate-equivalence")
    return {"topic_tags": topic_tags, "language": "da"} if topic_tags else {"language": "da"}


def _matches_metadata_filter(document: dict[str, Any], metadata_filter: dict[str, Any]) -> bool:
    language = metadata_filter.get("language")
    if language and document.get("language") != language:
        return False
    required_tags = set(metadata_filter.get("topic_tags", []))
    document_tags = set(document.get("topic_tags", []))
    return required_tags.issubset(document_tags)


def _fts_match_expression(text: str) -> str:
    tokens = []
    for token in TOKEN_PATTERN.findall(text):
        folded = token.casefold()
        if len(folded) < 2:
            continue
        if folded not in tokens:
            tokens.append(folded)
    return " OR ".join(tokens[:24])


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _report_install_phase(
    progress_callback: Any | None,
    phase: str,
    message: str,
    percent: int,
) -> None:
    if progress_callback is not None:
        progress_callback({"phase": phase, "message": message, "percent": percent})


def _inject_install_fault(fault_injector: Any | None, phase: str) -> None:
    if fault_injector is not None:
        fault_injector(phase)
