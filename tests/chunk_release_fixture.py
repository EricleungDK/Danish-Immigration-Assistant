"""Shared reviewed semantic-chunk release fixtures for issue #49 tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from danish_rag.knowledge_release import BUNDLED_MINIMAL_RELEASE
from danish_rag.source_maintenance import build_publishable_knowledge_release
from tests.release_trust_fixture import TestReleaseTrustFixture


def normalized_content_sha256(content: str) -> str:
    normalized_content = " ".join(content.split())
    return hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()


def bundled_reviewed_source_and_document(
    content: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads(
        (BUNDLED_MINIMAL_RELEASE / "manifest.json").read_text(encoding="utf-8")
    )
    documents = json.loads(
        (BUNDLED_MINIMAL_RELEASE / "corpus" / "documents.json").read_text(
            encoding="utf-8"
        )
    )
    source = {
        **manifest["sources"][0],
        "normalized_document_sha256": normalized_content_sha256(content),
    }
    document = {**documents[0], "content": content}
    return source, document


def build_chunked_release_fixture(
    *,
    release_dir: Path,
    release_id: str,
    source: dict[str, Any],
    document: dict[str, Any],
    release_trust: TestReleaseTrustFixture,
    created_at_utc: str,
) -> Path:
    build_publishable_knowledge_release(
        release_dir=release_dir,
        release_id=release_id,
        source_registry_version="sr-2026-07-30.1",
        sources=[source],
        documents=[document],
        created_at_utc=created_at_utc,
        minimum_application_version="0.1.0",
        corpus_schema_version="2.0",
        signing_private_key_path=release_trust.signing_private_key_path,
        trust_root_path=release_trust.trust_root_path,
    )
    return release_dir
