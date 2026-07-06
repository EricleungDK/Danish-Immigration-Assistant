"""Browser-test server for the local application setup flow."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import uvicorn

from danish_rag.local_app import create_app
from danish_rag.provider_setup import ProviderConfiguration


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
        return {
            "summary": "The reviewed source identifies Prøve i Dansk 2 for this supported question.",
            "sections": [
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
            ],
        }


CONFIG_PATH = Path(
    os.environ.get("DI_RAG_TEST_CONFIG_PATH", "/tmp/di-rag-browser-provider-config.json")
)
DATA_DIR = Path(os.environ.get("DI_RAG_TEST_DATA_DIR", "/tmp/di-rag-browser-data"))
if os.environ.get("DI_RAG_TEST_RESET_CONFIG") == "1":
    CONFIG_PATH.unlink(missing_ok=True)
    shutil.rmtree(DATA_DIR, ignore_errors=True)

app = create_app(
    config_path=CONFIG_PATH,
    data_dir=DATA_DIR,
    answer_generator=FixtureAnswerGenerator(),
    capability_tester=fixture_capability_tester,
)


def main() -> int:
    port = int(os.environ.get("DI_RAG_BROWSER_PORT", "8917"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
