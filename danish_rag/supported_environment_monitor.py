"""Real-process browser execution for supported-environment qualification.

This module owns the live execution mechanics so the release-monitor coordinator can
remain focused on policy and evidence evaluation. Browser output is deliberately
restricted to journey outcomes and runtime identities; questions, answers, evidence
content, and conversation identifiers stay inside the temporary workspace.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from .local_app import create_app


ROOT = Path(__file__).resolve().parents[1]
BROWSER_RUNNER_PATH = Path(__file__).with_name(
    "supported_environment_browser.mjs"
)
LOOPBACK_HOST = "127.0.0.1"
SERVER_READY_TIMEOUT_SECONDS = 60.0
BROWSER_PHASE_TIMEOUT_SECONDS = 15 * 60


class SupportedEnvironmentExecutionError(RuntimeError):
    """Raised when real-process or browser evidence cannot be established."""


@dataclass(frozen=True)
class LiveEnvironmentWorkspace:
    """Paths prepared by the release-monitor coordinator for live execution."""

    data_dir: Path
    config_path: Path
    release_catalog_dir: Path
    trust_root_path: Path
    target_release_id: str


@dataclass
class _ApplicationProcess:
    process: subprocess.Popen[bytes]
    log_stream: Any


WorkspacePreparer = Callable[[Path], LiveEnvironmentWorkspace]


def execute_live_supported_environment_journeys(
    *,
    policy: dict[str, Any],
    prepare_workspace: WorkspacePreparer,
    project_root: Path = ROOT,
) -> dict[str, Any]:
    """Run two browser phases around an actual local-app process restart."""

    node = shutil.which("node")
    if node is None or not BROWSER_RUNNER_PATH.is_file():
        raise SupportedEnvironmentExecutionError(
            "Playwright browser execution is unavailable."
        )

    provider_policy = policy["providers"]["initial"]
    generation_policy = policy["models"]["generation"]
    provider_endpoint = str(provider_policy["default_endpoint"])
    generation_model = str(generation_policy["initial"])

    with tempfile.TemporaryDirectory(
        prefix="di-rag-live-environment-monitor-"
    ) as temporary:
        temporary_root = Path(temporary)
        workspace = prepare_workspace(temporary_root)
        port = _available_loopback_port()
        base_url = f"http://{LOOPBACK_HOST}:{port}"
        state_path = temporary_root / "browser-private-state.json"
        phase_one_path = temporary_root / "browser-phase-one.json"
        phase_two_path = temporary_root / "browser-phase-two.json"

        starts = 0
        stops = 0
        browser_phases = 0
        first_process_stopped = False

        first = _start_application(
            workspace=workspace,
            port=port,
            project_root=project_root,
            log_path=temporary_root / "app-phase-one.log",
        )
        try:
            _wait_for_application(first, base_url=base_url)
            starts += 1
            phase_one = _run_browser_phase(
                node=node,
                phase="before-restart",
                base_url=base_url,
                provider_endpoint=provider_endpoint,
                generation_model=generation_model,
                target_release_id=workspace.target_release_id,
                state_path=state_path,
                output_path=phase_one_path,
                project_root=project_root,
            )
            browser_phases += 1
        finally:
            if _stop_application(first):
                stops += 1
                first_process_stopped = True

        if not first_process_stopped:
            raise SupportedEnvironmentExecutionError(
                "The first local app process did not stop cleanly."
            )

        second = _start_application(
            workspace=workspace,
            port=port,
            project_root=project_root,
            log_path=temporary_root / "app-phase-two.log",
        )
        try:
            _wait_for_application(second, base_url=base_url)
            starts += 1
            phase_two = _run_browser_phase(
                node=node,
                phase="after-restart",
                base_url=base_url,
                provider_endpoint=provider_endpoint,
                generation_model=generation_model,
                target_release_id=workspace.target_release_id,
                state_path=state_path,
                output_path=phase_two_path,
                project_root=project_root,
            )
            browser_phases += 1
        finally:
            if _stop_application(second):
                stops += 1

        phase_one_browser = phase_one["browser_identity"]
        phase_two_browser = phase_two["browser_identity"]
        if phase_one_browser != phase_two_browser:
            raise SupportedEnvironmentExecutionError(
                "Browser identity changed across restart phases."
            )

        journey_status = {
            **phase_one["journey_status"],
            **phase_two["journey_status"],
        }
        restart_observed = all(
            (
                first_process_stopped,
                starts == 2,
                stops == 2,
                journey_status.get("history-persistence") == "passed",
            )
        )
        observed_identity = observe_supported_environment_identity()
        observed_identity.update(
            {
                "ollama_version": str(
                    phase_one["runtime_configuration"].get(
                        "provider_version", ""
                    )
                ),
                "browser_name": phase_one_browser["name"],
                "browser_version": phase_one_browser["version"],
            }
        )
        return {
            "journey_status": journey_status,
            "diagnostics": [],
            "runtime_configuration": phase_one["runtime_configuration"],
            "corpus_identity": phase_two["corpus_identity"],
            "observed_environment_identity": observed_identity,
            "execution_evidence": {
                "transport": "loopback-bound-process",
                "browser_driver": "playwright",
                "browser_phase_count": browser_phases,
                "app_process_start_count": starts,
                "app_process_stop_count": stops,
                "history_restart_observed": restart_observed,
                "browser_evidence_available": browser_phases == 2,
            },
        }


def observe_supported_environment_identity() -> dict[str, str]:
    """Collect host/runtime facts without using policy values as observations."""

    os_release = _read_os_release(Path("/etc/os-release"))
    try:
        kernel_release = Path("/proc/sys/kernel/osrelease").read_text(
            encoding="utf-8"
        )
    except OSError:
        kernel_release = platform.release()
    kernel_folded = kernel_release.casefold()
    wsl_version = "2" if "wsl2" in kernel_folded else (
        "1" if "microsoft" in kernel_folded else ""
    )

    windows_build = _observed_windows_build() if wsl_version else ""
    windows_version = ""
    if windows_build:
        try:
            windows_version = "11" if int(windows_build) >= 22000 else "10"
        except ValueError:
            windows_version = ""

    return {
        "host_os": "Windows" if wsl_version and windows_build else platform.system(),
        "windows_version": windows_version,
        "windows_build": windows_build,
        "wsl_version": wsl_version,
        "distribution_id": os_release.get("ID", ""),
        "distribution_version": os_release.get("VERSION_ID", ""),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "ollama_version": "",
        "browser_name": "",
        "browser_version": "",
    }


def _read_os_release(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, raw_value = line.split("=", 1)
        values[key] = raw_value.strip().strip('"').strip("'")
    return values


def _observed_windows_build() -> str:
    command = Path("/mnt/c/Windows/System32/cmd.exe")
    if not command.is_file():
        return ""
    try:
        completed = subprocess.run(
            [str(command), "/d", "/c", "ver"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""
    match = re.search(
        r"(?:version|versione|versi[oó]n)\s+\d+\.\d+\.(\d+)",
        completed.stdout,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind((LOOPBACK_HOST, 0))
        return int(server.getsockname()[1])


def _start_application(
    *,
    workspace: LiveEnvironmentWorkspace,
    port: int,
    project_root: Path,
    log_path: Path,
) -> _ApplicationProcess:
    log_stream = log_path.open("wb")
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-m",
                "danish_rag.supported_environment_monitor",
                "serve",
                "--host",
                LOOPBACK_HOST,
                "--port",
                str(port),
                "--config-path",
                str(workspace.config_path),
                "--data-dir",
                str(workspace.data_dir),
                "--release-catalog-dir",
                str(workspace.release_catalog_dir),
                "--trust-root-path",
                str(workspace.trust_root_path),
            ],
            cwd=project_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
        )
    except Exception:
        log_stream.close()
        raise
    return _ApplicationProcess(process=process, log_stream=log_stream)


def _wait_for_application(
    application: _ApplicationProcess,
    *,
    base_url: str,
) -> None:
    deadline = time.monotonic() + SERVER_READY_TIMEOUT_SECONDS
    with httpx.Client(timeout=1.0, trust_env=False) as client:
        while time.monotonic() < deadline:
            if application.process.poll() is not None:
                raise SupportedEnvironmentExecutionError(
                    "The local app process exited before readiness."
                )
            try:
                response = client.get(f"{base_url}/status")
                if response.status_code == 200 and isinstance(response.json(), dict):
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.1)
    raise SupportedEnvironmentExecutionError(
        "The local app process did not become ready."
    )


def _stop_application(application: _ApplicationProcess) -> bool:
    try:
        if application.process.poll() is None:
            application.process.terminate()
            try:
                application.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                application.process.kill()
                application.process.wait(timeout=10)
        return application.process.poll() is not None
    finally:
        application.log_stream.close()


def _run_browser_phase(
    *,
    node: str,
    phase: str,
    base_url: str,
    provider_endpoint: str,
    generation_model: str,
    target_release_id: str,
    state_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    try:
        process = subprocess.Popen(
            [
                node,
                str(BROWSER_RUNNER_PATH),
                "--phase",
                phase,
                "--base-url",
                base_url,
                "--provider-endpoint",
                provider_endpoint,
                "--generation-model",
                generation_model,
                "--target-release-id",
                target_release_id,
                "--state-path",
                str(state_path),
                "--output-path",
                str(output_path),
            ],
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            process.communicate(timeout=BROWSER_PHASE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            _terminate_process_group(process)
            raise SupportedEnvironmentExecutionError(
                "The Playwright browser phase timed out."
            ) from exc
        if process.returncode != 0:
            raise SupportedEnvironmentExecutionError(
                "The Playwright browser phase failed."
            )
    except OSError as exc:
        raise SupportedEnvironmentExecutionError(
            "The Playwright browser phase failed."
        ) from exc
    return _read_browser_phase_output(output_path, phase=phase)


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()


def _read_browser_phase_output(path: Path, *, phase: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SupportedEnvironmentExecutionError(
            "The Playwright browser phase produced no valid evidence."
        ) from exc
    if not isinstance(value, dict):
        raise SupportedEnvironmentExecutionError(
            "The Playwright browser evidence has an invalid shape."
        )

    expected_journeys = (
        ("setup", "supported-answer", "refusal", "evidence-inspection")
        if phase == "before-restart"
        else ("history-persistence", "deletion-export", "update-installation")
    )
    status = value.get("journey_status")
    if not isinstance(status, dict) or set(status) != set(expected_journeys):
        raise SupportedEnvironmentExecutionError(
            "The Playwright journey evidence is incomplete."
        )
    if any(status.get(journey_id) != "passed" for journey_id in expected_journeys):
        raise SupportedEnvironmentExecutionError(
            "A Playwright critical journey did not pass."
        )

    browser = value.get("browser_identity")
    if not isinstance(browser, dict):
        raise SupportedEnvironmentExecutionError(
            "The Playwright browser identity is missing."
        )
    browser_identity = {
        "name": str(browser.get("name", "")),
        "version": str(browser.get("version", "")),
    }
    if not all(browser_identity.values()):
        raise SupportedEnvironmentExecutionError(
            "The Playwright browser identity is incomplete."
        )

    result: dict[str, Any] = {
        "journey_status": {
            journey_id: "passed" for journey_id in expected_journeys
        },
        "browser_identity": browser_identity,
    }
    if phase == "before-restart":
        configuration = value.get("runtime_configuration")
        if not isinstance(configuration, dict):
            raise SupportedEnvironmentExecutionError(
                "The observed runtime configuration is missing."
            )
        result["runtime_configuration"] = {
            "provider_id": str(configuration.get("provider_id", "")),
            "provider_version": str(configuration.get("provider_version", "")),
            "model": str(configuration.get("model", "")),
            "model_identity": _safe_model_identity(
                configuration.get("model_identity")
            ),
        }
    else:
        corpus = value.get("corpus_identity")
        if not isinstance(corpus, dict):
            raise SupportedEnvironmentExecutionError(
                "The observed corpus identity is missing."
            )
        result["corpus_identity"] = {
            key: str(corpus.get(key, ""))
            for key in (
                "knowledge_release_id",
                "corpus_id",
                "source_registry_version",
                "embedding_model",
                "embedding_vector_dimensions",
                "index_schema_version",
            )
        }
    return result


def _safe_model_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "architecture",
        "digest",
        "family",
        "format",
        "model",
        "parameter_size",
        "quantization_level",
    }
    return {
        str(key): item
        for key, item in value.items()
        if key in allowed and isinstance(item, str | int | float | bool)
    }


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    app = create_app(
        config_path=args.config_path,
        data_dir=args.data_dir,
        release_catalog_dir=args.release_catalog_dir,
        trust_root_path=args.trust_root_path,
    )
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",
        access_log=False,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve")
    serve.add_argument("--host", required=True)
    serve.add_argument("--port", required=True, type=int)
    serve.add_argument("--config-path", required=True, type=Path)
    serve.add_argument("--data-dir", required=True, type=Path)
    serve.add_argument("--release-catalog-dir", required=True, type=Path)
    serve.add_argument("--trust-root-path", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "serve":
        return _serve(args)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
