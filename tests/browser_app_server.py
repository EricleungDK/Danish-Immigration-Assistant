"""Browser-test server for the local application setup flow."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import uvicorn

from danish_rag.knowledge_release import BUNDLED_MINIMAL_RELEASE
from danish_rag.local_app import create_app
from danish_rag.provider_setup import ProviderConfiguration
from danish_rag.source_maintenance import build_publishable_knowledge_release


def fixture_capability_tester(configuration: ProviderConfiguration) -> dict[str, Any]:
    if configuration.model == "fail-model":
        return {
            "ok": False,
            "reason": "service_unreachable",
            "message": "Provider service is unreachable. Start the local server and retry.",
        }
    return {
        "ok": True,
        "reason": "passed",
        "message": "Provider capability test passed.",
        "provider_version": "browser-fixture",
        "model_identity": {"id": configuration.model},
        "capabilities": ["generation"],
    }


class FixtureAnswerGenerator:
    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        citation_id = evidence[0]["citation_id"]
        sections = [
            {
                "kind": "official_fact",
                "text": (
                    "Permanent opholdstilladelse can require documentation for "
                    "bestået Prøve i Dansk 2 or an equivalent Danish test."
                ),
                "citation_ids": [citation_id],
            },
            {
                "kind": "interpretation",
                "text": (
                    "Use Prøve i Dansk 2 as the Danish term to recognize on the "
                    "official page, without treating this as a personal eligibility decision."
                ),
                "citation_ids": [citation_id],
            },
        ]
        if "warning" in question.casefold():
            sections.append(
                {
                    "kind": "source_warning",
                    "text": "Review the cited official source before relying on freshness-sensitive details.",
                    "citation_ids": [citation_id],
                }
            )
        return {
            "summary": "The reviewed source identifies Prøve i Dansk 2 for this supported question.",
            "sections": sections,
        }


CONFIG_PATH = Path(
    os.environ.get("DI_RAG_TEST_CONFIG_PATH", "/tmp/di-rag-browser-provider-config.json")
)
DATA_DIR = Path(os.environ.get("DI_RAG_TEST_DATA_DIR", "/tmp/di-rag-browser-data"))
RELEASE_CATALOG = Path(
    os.environ.get("DI_RAG_TEST_RELEASE_CATALOG", "/tmp/di-rag-browser-release-catalog")
)
if os.environ.get("DI_RAG_TEST_RESET_CONFIG") == "1":
    CONFIG_PATH.unlink(missing_ok=True)
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    shutil.rmtree(RELEASE_CATALOG, ignore_errors=True)


def ensure_browser_release_catalog() -> None:
    release_id = "kr-2026-07-07.1"
    release_dir = RELEASE_CATALOG / release_id
    if release_dir.exists():
        return

    current_manifest = json.loads(
        (BUNDLED_MINIMAL_RELEASE / "manifest.json").read_text(encoding="utf-8")
    )
    current_documents = json.loads(
        (BUNDLED_MINIMAL_RELEASE / "corpus" / "documents.json").read_text(
            encoding="utf-8"
        )
    )
    sources = []
    for source in current_manifest["sources"]:
        updated = dict(source)
        if updated["source_id"] == "nyidanmark-permanent-residence-language-requirements":
            updated["last_checked_at_utc"] = "2026-07-07T12:00:00Z"
            updated["reviewed_at_utc"] = "2026-07-07T12:30:00Z"
            updated["source_content_sha256"] = (
                "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
            )
            updated["normalized_document_sha256"] = (
                "ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100"
            )
        sources.append(updated)

    documents = []
    for document in current_documents:
        updated = dict(document)
        if updated["source_id"] == "nyidanmark-permanent-residence-language-requirements":
            updated["checked_at_utc"] = "2026-07-07T12:00:00Z"
            updated["content"] = updated["content"] + "\nReviewed browser update."
        documents.append(updated)

    build_publishable_knowledge_release(
        release_dir=release_dir,
        release_id=release_id,
        source_registry_version="sr-2026-07-07.1",
        sources=sources,
        documents=documents,
        created_at_utc="2026-07-07T13:00:00Z",
        minimum_application_version="0.1.0",
    )


ensure_browser_release_catalog()

app = create_app(
    config_path=CONFIG_PATH,
    data_dir=DATA_DIR,
    release_catalog_dir=RELEASE_CATALOG,
    answer_generator=FixtureAnswerGenerator(),
    capability_tester=fixture_capability_tester,
)


def main() -> int:
    port = int(os.environ.get("DI_RAG_BROWSER_PORT", "8917"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
