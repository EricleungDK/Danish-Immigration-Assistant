"""Browser-test server for the local application setup flow."""

from __future__ import annotations

import os
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


CONFIG_PATH = Path(
    os.environ.get("DI_RAG_TEST_CONFIG_PATH", "/tmp/di-rag-browser-provider-config.json")
)
if os.environ.get("DI_RAG_TEST_RESET_CONFIG") == "1":
    CONFIG_PATH.unlink(missing_ok=True)

app = create_app(
    config_path=CONFIG_PATH,
    capability_tester=fixture_capability_tester,
)


def main() -> int:
    uvicorn.run(app, host="127.0.0.1", port=8917, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
