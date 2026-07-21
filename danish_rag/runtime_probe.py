"""Live runtime probe for the issue #26 Ollama baseline."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from .runtime_policy import RuntimePolicy, is_loopback_url, load_runtime_policy


class ProbeExitStatus(IntEnum):
    """Stable process outcomes for the public runtime-probe CLI contract."""

    PASSED = 0
    INCOMPLETE = 1
    PROVIDER_UNREACHABLE = 2
    PROVIDER_VERSION_UNSUPPORTED = 3
    MODEL_UNAVAILABLE = 4
    STRUCTURED_RESPONSE_INVALID = 5
    NON_LOOPBACK_ENDPOINT = 6


class ProviderProbeEvidence(TypedDict, total=False):
    id: str
    endpoint: str
    version: str


class ModelIdentityEvidence(TypedDict):
    family: str
    architecture: str
    quantization_level: str


class ModelProbeEvidence(TypedDict, total=False):
    name: str
    capabilities: list[str]
    details: dict[str, Any]
    model_info: dict[str, Any]
    identity: ModelIdentityEvidence


class StructuredProbeResponse(TypedDict, total=False):
    runtime_baseline: str
    status: Literal["ok"]


class EnvironmentEvidence(TypedDict):
    python_version: str
    platform_system: str
    platform_release: str
    machine: str
    processor: str
    wsl: bool
    cpu_count: int | None
    memory_total_mb: int | None


@dataclass
class ProbeResult:
    exit_status: ProbeExitStatus
    diagnostic: str
    environment: EnvironmentEvidence
    provider: ProviderProbeEvidence = field(default_factory=dict)
    model: ModelProbeEvidence = field(default_factory=dict)
    structured_response: StructuredProbeResponse = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)
    command: list[str] = field(default_factory=list)
    started_at_utc: str = ""
    finished_at_utc: str = ""
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OllamaClient:
    def __init__(self, endpoint: str, timeout_seconds: float = 60.0):
        self.endpoint = endpoint.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def get_version(self) -> dict[str, Any]:
        return self._request("GET", "/api/version")

    def show_model(self, model: str) -> dict[str, Any]:
        try:
            return self._request("POST", "/api/show", {"model": model})
        except urllib.error.HTTPError as exc:
            if exc.code in {400, 404}:
                raise FileNotFoundError(model) from exc
            raise

    def chat_structured(
        self,
        *,
        model: str,
        schema: dict[str, Any],
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/chat",
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "format": schema,
                "options": {"temperature": 0},
            },
        )

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            f"{self.endpoint}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


def run_runtime_probe(
    policy: RuntimePolicy,
    *,
    client: Any | None = None,
    command: list[str] | None = None,
) -> ProbeResult:
    started = datetime.now(UTC)
    started_counter = time.perf_counter()
    timings: dict[str, float] = {}
    environment = collect_environment()

    provider_policy = policy["providers"]["initial"]
    endpoint = provider_policy["default_endpoint"]
    model_name = policy["models"]["generation"]["initial"]

    result = ProbeResult(
        exit_status=ProbeExitStatus.INCOMPLETE,
        diagnostic="runtime probe did not complete",
        provider={"id": provider_policy["id"], "endpoint": endpoint},
        model={"name": model_name},
        timings_ms=timings,
        environment=environment,
        command=command or [],
        started_at_utc=started.isoformat(),
    )

    if not is_loopback_url(endpoint):
        return _finish(
            result,
            ProbeExitStatus.NON_LOOPBACK_ENDPOINT,
            "Configured Ollama endpoint is not loopback. Use http://127.0.0.1:11434 for the issue #26 baseline.",
            started_counter,
        )

    client = client or OllamaClient(endpoint)

    try:
        version_payload = _timed(timings, "service_version", client.get_version)
    except Exception as exc:
        return _finish(
            result,
            ProbeExitStatus.PROVIDER_UNREACHABLE,
            f"Ollama service is unreachable at {endpoint}. Start Ollama and confirm the loopback API is listening. Detail: {exc}",
            started_counter,
        )

    version = str(version_payload.get("version", ""))
    result.provider["version"] = version
    minimum_version = provider_policy["minimum_version"]
    if _version_tuple(version) < _version_tuple(minimum_version):
        return _finish(
            result,
            ProbeExitStatus.PROVIDER_VERSION_UNSUPPORTED,
            f"Upgrade Ollama to {minimum_version} or newer before using this baseline. Found {version or 'unknown'}.",
            started_counter,
        )

    try:
        model_payload = _timed(timings, "model_inspection", client.show_model, model_name)
    except FileNotFoundError:
        return _finish(
            result,
            ProbeExitStatus.MODEL_UNAVAILABLE,
            f"{model_name} is not installed. Install it with `ollama pull {model_name}` and rerun the probe.",
            started_counter,
        )
    except Exception as exc:
        return _finish(
            result,
            ProbeExitStatus.MODEL_UNAVAILABLE,
            f"Could not inspect {model_name}. Confirm the model is installed and usable. Detail: {exc}",
            started_counter,
        )

    capabilities = _model_capabilities(model_payload)
    result.model.update(
        {
            "name": model_name,
            "capabilities": capabilities,
            "details": model_payload.get("details", {}),
            "model_info": _summarize_model_info(model_payload.get("model_info", {})),
        }
    )
    try:
        result.model["identity"] = _validate_model_identity(
            model_payload, policy["models"]["generation"].get("identity", {})
        )
    except Exception as exc:
        return _finish(
            result,
            ProbeExitStatus.MODEL_UNAVAILABLE,
            f"The installed generation model did not match the issue #26 model identity baseline. Detail: {exc}",
            started_counter,
        )

    if "completion" not in capabilities:
        return _finish(
            result,
            ProbeExitStatus.MODEL_UNAVAILABLE,
            f"The installed generation model is missing the completion capability required by the issue #26 baseline. "
            f"/api/show capabilities reported: {capabilities!r}.",
            started_counter,
        )

    schema = _structured_probe_schema(policy["baseline_id"])
    messages = [
        {
            "role": "system",
            "content": "Return only JSON that matches the provided schema.",
        },
        {
            "role": "user",
            "content": "Return the runtime baseline identifier and status ok.",
        },
    ]
    try:
        chat_payload = _timed(
            timings,
            "structured_completion",
            client.chat_structured,
            model=model_name,
            schema=schema,
            messages=messages,
        )
        structured_response = _parse_structured_response(chat_payload)
        _validate_structured_response(structured_response, policy["baseline_id"])
    except Exception as exc:
        return _finish(
            result,
            ProbeExitStatus.STRUCTURED_RESPONSE_INVALID,
            f"The structured JSON response did not match the issue #26 schema. Detail: {exc}",
            started_counter,
        )

    result.structured_response = structured_response
    return _finish(
        result,
        ProbeExitStatus.PASSED,
        "Runtime baseline probe passed.",
        started_counter,
    )


def collect_environment() -> EnvironmentEvidence:
    return {
        "python_version": platform.python_version(),
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "wsl": _running_under_wsl(),
        "cpu_count": os.cpu_count(),
        "memory_total_mb": _linux_memory_total_mb(),
    }


def write_evidence(result: ProbeResult, path: str | Path) -> None:
    evidence_path = Path(path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = evidence_path.with_suffix(evidence_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(evidence_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        default="config/runtime-policy.json",
        help="Path to the runtime policy JSON file.",
    )
    parser.add_argument(
        "--evidence",
        default="docs/progress/issue-26-runtime-probe.json",
        help="Path where probe evidence JSON should be written atomically.",
    )
    parser.add_argument(
        "--timeout",
        default=60.0,
        type=float,
        help="Timeout in seconds for each Ollama HTTP request.",
    )
    args = parser.parse_args(argv)

    policy = load_runtime_policy(args.policy)
    endpoint = policy["providers"]["initial"]["default_endpoint"]
    client = OllamaClient(endpoint, timeout_seconds=args.timeout)
    command = [sys.executable, "-m", "danish_rag.runtime_probe", *sys.argv[1:]]
    result = run_runtime_probe(policy, client=client, command=command)
    write_evidence(result, args.evidence)

    print(result.diagnostic)
    print(f"Evidence: {args.evidence}")
    return result.exit_status


def _finish(
    result: ProbeResult,
    exit_status: ProbeExitStatus,
    diagnostic: str,
    started_counter: float,
) -> ProbeResult:
    result.exit_status = exit_status
    result.diagnostic = diagnostic
    result.finished_at_utc = datetime.now(UTC).isoformat()
    result.elapsed_ms = round((time.perf_counter() - started_counter) * 1000, 3)
    return result


def _timed(timings: dict[str, float], name: str, func: Any, *args: Any, **kwargs: Any) -> Any:
    started = time.perf_counter()
    try:
        return func(*args, **kwargs)
    finally:
        timings[name] = round((time.perf_counter() - started) * 1000, 3)


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in version.split("."):
        digits = ""
        for character in part:
            if not character.isdigit():
                break
            digits += character
        if digits:
            parts.append(int(digits))
    return tuple(parts)


def _model_capabilities(model_payload: dict[str, Any]) -> list[str]:
    capabilities = model_payload.get("capabilities")
    if isinstance(capabilities, list):
        return [str(capability) for capability in capabilities]
    return []


def _summarize_model_info(model_info: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "general.architecture",
        "general.parameter_count",
        "general.quantization_version",
        "gemma3.context_length",
        "gemma3.embedding_length",
        "gemma3.attention.head_count",
    }
    return {key: model_info[key] for key in allowed_keys if key in model_info}


def _validate_model_identity(
    model_payload: dict[str, Any], identity_policy: dict[str, Any]
) -> dict[str, str]:
    details = model_payload.get("details")
    if not isinstance(details, dict):
        details = {}
    model_info = model_payload.get("model_info")
    if not isinstance(model_info, dict):
        model_info = {}

    expected_family = str(identity_policy.get("family", ""))
    expected_architecture = str(identity_policy.get("architecture", ""))
    expected_quantization = str(identity_policy.get("quantization_level", ""))

    family = _model_family(details)
    architecture = model_info.get("general.architecture")
    quantization = details.get("quantization_level")

    missing_fields = [
        field
        for field, value in (
            ("details.family", family),
            ("model_info.general.architecture", architecture),
            ("details.quantization_level", quantization),
        )
        if not value
    ]
    if missing_fields:
        raise ValueError(
            "missing identity evidence from /api/show: " + ", ".join(missing_fields)
        )

    if expected_family and family != expected_family:
        raise ValueError(
            f"family expected {expected_family!r} from /api/show details, found {family!r}. "
            f"Reinstall the approved {expected_family} artifact."
        )
    if expected_architecture and architecture != expected_architecture:
        raise ValueError(
            f"architecture expected {expected_architecture!r} from /api/show model_info, "
            f"found {architecture!r}."
        )
    if expected_quantization and quantization != expected_quantization:
        raise ValueError(
            f"quantization expected {expected_quantization!r} from /api/show details, "
            f"found {quantization!r}. Pull the approved artifact again."
        )

    return {
        "family": str(family),
        "architecture": str(architecture),
        "quantization_level": str(quantization),
    }


def _model_family(details: dict[str, Any]) -> str | None:
    family = details.get("family")
    if isinstance(family, str) and family:
        return family
    families = details.get("families")
    if isinstance(families, list):
        for candidate in families:
            if isinstance(candidate, str) and candidate:
                return candidate
    return None


def _structured_probe_schema(baseline_id: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "runtime_baseline": {"const": baseline_id},
            "status": {"const": "ok"},
        },
        "required": ["runtime_baseline", "status"],
        "additionalProperties": False,
    }


def _parse_structured_response(
    chat_payload: dict[str, Any],
) -> StructuredProbeResponse:
    content = chat_payload.get("message", {}).get("content")
    if not isinstance(content, str):
        raise ValueError("chat response did not include message.content")
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("chat response JSON was not an object")
    return cast(StructuredProbeResponse, parsed)


def _validate_structured_response(
    response: StructuredProbeResponse, baseline_id: str
) -> None:
    expected_fields = {"runtime_baseline", "status"}

    missing = sorted(field for field in expected_fields if field not in response)
    if missing:
        raise ValueError("missing required field(s): " + ", ".join(missing))

    extra = sorted(field for field in response if field not in expected_fields)
    if extra:
        raise ValueError(
            "unexpected field(s): "
            + ", ".join(extra)
            + "; expected only "
            + ", ".join(sorted(expected_fields))
        )

    if response["runtime_baseline"] != baseline_id:
        raise ValueError(
            f"runtime_baseline expected {baseline_id!r}, "
            f"found {response['runtime_baseline']!r}"
        )
    if response["status"] != "ok":
        raise ValueError(f"status expected 'ok', found {response['status']!r}")


def _running_under_wsl() -> bool:
    try:
        version_text = Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False
    return "microsoft" in version_text or "wsl" in version_text


def _linux_memory_total_mb() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024)
    except OSError:
        return None
    return None


if __name__ == "__main__":
    raise SystemExit(main())
