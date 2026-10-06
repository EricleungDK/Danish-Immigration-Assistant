"""Run the real local app in a fresh, isolated workspace for the demo recording.

The workspace gets its own XDG config/data directories (never the user's), a
provider configuration verified against the real local Ollama model, and the
named signed knowledge release installed from the repository catalogue. The app
uses that local catalogue, so it makes no GitHub release calls while recording.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Callable

from danish_rag.knowledge_release import (
    DEFAULT_RELEASE_CATALOG_DIR,
    DEFAULT_TRUST_ROOTS_DIR,
    install_knowledge_release,
)
from danish_rag.provider_setup import (
    ProviderCapabilityTester,
    ProviderConfiguration,
    save_provider_configuration,
    validated_configuration,
)

APP_DIR = "danish-immigration-rag"
OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
TRUST_ROOT = DEFAULT_TRUST_ROOTS_DIR / "project-release-key-v2.json"


def isolated_environment(root: Path, *, home: Path | None = None) -> dict[str, str]:
    """XDG variables for a fresh workspace; refuses the user's real directories."""

    home = (home or Path.home()).resolve()
    root = root.resolve()
    for real in (home / ".config", home / ".local" / "share"):
        if root == real or real in root.parents:
            raise ValueError(f"Workspace {root} is inside the user's real {real}.")
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Workspace {root} must be new or empty.")
    return {"XDG_CONFIG_HOME": str(root / "config"), "XDG_DATA_HOME": str(root / "data")}


def prepare_workspace(
    env: dict[str, str],
    *,
    model: str,
    release_id: str,
    tester: Callable[[ProviderConfiguration], Any] | None = None,
    installer: Callable[..., Any] = install_knowledge_release,
    preload: Callable[[str], None] | None = None,
    attempts: int = 5,
) -> None:
    """Verify the local model, save its configuration, install the release."""

    tester = tester or ProviderCapabilityTester(timeout_seconds=180)
    configuration = ProviderConfiguration(provider_id="ollama", endpoint=OLLAMA_ENDPOINT, model=model)
    (preload or _preload_ollama_model)(model)
    result = None
    for _ in range(attempts):  # a cold model can time out its first structured test
        result = tester(configuration)
        if result.ok:
            break
    if result is None or not result.ok:
        raise RuntimeError(f"Local model capability test failed: {result.message if result else 'not run'}")
    config_path = Path(env["XDG_CONFIG_HOME"]) / APP_DIR / "provider-config.json"
    save_provider_configuration(config_path, validated_configuration(configuration, result))
    installer(
        Path(env["XDG_DATA_HOME"]) / APP_DIR,
        release_dir=DEFAULT_RELEASE_CATALOG_DIR / release_id,
        trust_root_path=TRUST_ROOT,
        expected_release_id=release_id,
    )


def _preload_ollama_model(model: str) -> None:
    """Load the model before the setup probe; its first answer after a cold load can be empty."""

    import json
    import urllib.request

    request = urllib.request.Request(
        f"{OLLAMA_ENDPOINT}/api/generate",
        data=json.dumps({"model": model, "keep_alive": "15m"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        response.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", default="gemma4:12b")
    parser.add_argument("--release", default="kr-2026-09-05.1")
    args = parser.parse_args(argv)

    env = isolated_environment(args.workspace)
    os.environ.update(env)  # the app resolves its config/data paths from these
    prepare_workspace(env, model=args.model, release_id=args.release)

    import uvicorn

    from danish_rag.local_app import create_app

    app = create_app(release_catalog_dir=DEFAULT_RELEASE_CATALOG_DIR)
    import danish_rag

    print(f"READY http://127.0.0.1:{args.port}/ app={Path(danish_rag.__file__).parent}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
