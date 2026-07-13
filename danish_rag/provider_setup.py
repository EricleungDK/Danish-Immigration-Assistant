"""Provider setup, capability testing, and local configuration persistence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import urllib.error
import urllib.request

from .privacy_boundary import PrivacyBoundaryError, require_loopback_endpoint
from .runtime_policy import is_loopback_url, load_runtime_policy
from .runtime_probe import OllamaClient, run_runtime_probe


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = ROOT / "config" / "runtime-policy.json"


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    name: str
    description: str
    default_endpoint: str
    default_model: str


@dataclass(frozen=True)
class ProviderConfiguration:
    provider_id: str
    endpoint: str
    model: str
    provider_version: str = ""
    model_identity: dict[str, Any] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    validated_at_utc: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        provider = PROVIDERS[self.provider_id]
        return {
            "provider_id": self.provider_id,
            "provider_name": provider.name,
            "endpoint": self.endpoint,
            "model": self.model,
            "provider_version": self.provider_version,
            "model_identity": self.model_identity,
            "capabilities": self.capabilities,
            "validated_at_utc": self.validated_at_utc,
        }


@dataclass(frozen=True)
class CapabilityTestResult:
    ok: bool
    reason: str
    message: str
    provider_version: str = ""
    model_identity: dict[str, Any] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)

    @classmethod
    def from_value(cls, value: "CapabilityTestResult | dict[str, Any]") -> "CapabilityTestResult":
        if isinstance(value, cls):
            return value
        return cls(
            ok=bool(value.get("ok")),
            reason=str(value.get("reason", "")),
            message=str(value.get("message", "")),
            provider_version=str(value.get("provider_version", "")),
            model_identity=dict(value.get("model_identity") or {}),
            capabilities=[str(item) for item in value.get("capabilities", [])],
        )


PROVIDERS: dict[str, ProviderDefinition] = {
    "ollama": ProviderDefinition(
        id="ollama",
        name="Ollama",
        description="Issue #26 baseline provider for gemma4:12b on the local loopback API.",
        default_endpoint="http://127.0.0.1:11434",
        default_model="gemma4:12b",
    ),
    "openai_compatible": ProviderDefinition(
        id="openai_compatible",
        name="OpenAI-compatible local server",
        description="A local server with /v1/models and /v1/chat/completions endpoints.",
        default_endpoint="http://127.0.0.1:1234",
        default_model="local-model",
    ),
}


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(base) if base else Path.home() / ".config"
    return config_home / "danish-immigration-rag" / "provider-config.json"


def provider_options() -> list[ProviderDefinition]:
    return list(PROVIDERS.values())


def normalize_provider_form(form: dict[str, str]) -> ProviderConfiguration:
    provider_id = form.get("provider_id", "").strip()
    endpoint = form.get("endpoint", "").strip().rstrip("/")
    model = form.get("model", "").strip()
    return ProviderConfiguration(provider_id=provider_id, endpoint=endpoint, model=model)


def validate_provider_configuration(configuration: ProviderConfiguration) -> list[str]:
    failures: list[str] = []
    if configuration.provider_id not in PROVIDERS:
        failures.append("Choose a supported local generation-model provider.")
    if not configuration.endpoint:
        failures.append("Enter the provider endpoint.")
    else:
        parsed = urlparse(configuration.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            failures.append("Enter the endpoint as a full local URL, for example http://127.0.0.1:11434.")
        elif not is_loopback_url(configuration.endpoint):
            failures.append("Use a loopback endpoint such as http://127.0.0.1 or http://localhost.")
    if not configuration.model:
        failures.append("Enter the generation model to test.")
    return failures


def load_provider_configuration(path: str | Path) -> ProviderConfiguration | None:
    config_path = Path(path)
    if not config_path.exists():
        return None

    data = json.loads(config_path.read_text(encoding="utf-8"))
    configuration = ProviderConfiguration(
        provider_id=str(data["provider_id"]),
        endpoint=str(data["endpoint"]),
        model=str(data["model"]),
        provider_version=str(data.get("provider_version", "")),
        model_identity=dict(data.get("model_identity") or {}),
        capabilities=[str(item) for item in data.get("capabilities", [])],
        validated_at_utc=str(data.get("validated_at_utc", "")),
    )
    failures = validate_provider_configuration(configuration)
    if failures:
        raise ValueError("; ".join(failures))
    return configuration


def save_provider_configuration(path: str | Path, configuration: ProviderConfiguration) -> None:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = config_path.with_suffix(config_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(configuration.to_public_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(config_path)


def validated_configuration(
    configuration: ProviderConfiguration,
    result: CapabilityTestResult,
) -> ProviderConfiguration:
    return ProviderConfiguration(
        provider_id=configuration.provider_id,
        endpoint=configuration.endpoint,
        model=configuration.model,
        provider_version=result.provider_version,
        model_identity=result.model_identity,
        capabilities=result.capabilities,
        validated_at_utc=datetime.now(UTC).isoformat(),
    )


class ProviderCapabilityTester:
    def __init__(
        self,
        *,
        policy_path: str | Path = DEFAULT_POLICY_PATH,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.policy = load_runtime_policy(policy_path)
        self.timeout_seconds = timeout_seconds

    def __call__(self, configuration: ProviderConfiguration) -> CapabilityTestResult:
        failures = validate_provider_configuration(configuration)
        if failures:
            return CapabilityTestResult(
                ok=False,
                reason="invalid_configuration",
                message=" ".join(failures),
            )
        if configuration.provider_id == "ollama":
            return self._test_ollama(configuration)
        if configuration.provider_id == "openai_compatible":
            return self._test_openai_compatible(configuration)
        return CapabilityTestResult(
            ok=False,
            reason="unsupported_provider",
            message="Choose one of the supported local generation-model providers.",
        )

    def _test_ollama(self, configuration: ProviderConfiguration) -> CapabilityTestResult:
        policy = deepcopy(self.policy)
        policy["providers"]["initial"]["default_endpoint"] = configuration.endpoint
        policy["models"]["generation"]["initial"] = configuration.model
        client = OllamaClient(configuration.endpoint, timeout_seconds=self.timeout_seconds)
        result = run_runtime_probe(policy, client=client)
        if result.exit_status == 0:
            return CapabilityTestResult(
                ok=True,
                reason="passed",
                message="Provider capability test passed.",
                provider_version=str(result.provider.get("version", "")),
                model_identity=dict(result.model.get("identity") or {}),
                capabilities=[str(item) for item in result.model.get("capabilities", [])],
            )

        reason_by_exit_status = {
            2: "service_unreachable",
            3: "incompatible_provider_version",
            4: "model_unavailable",
            5: "structured_output_failed",
            6: "invalid_endpoint",
        }
        return CapabilityTestResult(
            ok=False,
            reason=reason_by_exit_status.get(result.exit_status, "capability_test_failed"),
            message=result.diagnostic,
            provider_version=str(result.provider.get("version", "")),
            model_identity=dict(result.model.get("identity") or {}),
            capabilities=[str(item) for item in result.model.get("capabilities", [])],
        )

    def _test_openai_compatible(self, configuration: ProviderConfiguration) -> CapabilityTestResult:
        try:
            models_payload = self._request_openai_json(
                configuration.endpoint,
                "GET",
                "/v1/models",
            )
        except Exception as exc:
            return CapabilityTestResult(
                ok=False,
                reason="service_unreachable",
                message=(
                    "Provider service is unreachable. Start the local server, confirm "
                    f"{configuration.endpoint}/v1/models responds, and retry. Detail: {exc}"
                ),
            )

        model_ids = _openai_model_ids(models_payload)
        if model_ids and configuration.model not in model_ids:
            return CapabilityTestResult(
                ok=False,
                reason="model_unavailable",
                message=(
                    f"{configuration.model} was not listed by the local provider. "
                    "Load the model in the provider and retry setup."
                ),
            )

        schema = {
            "type": "object",
            "properties": {"status": {"const": "ok"}},
            "required": ["status"],
            "additionalProperties": False,
        }
        payload = {
            "model": configuration.model,
            "messages": [
                {"role": "system", "content": "Return only JSON."},
                {"role": "user", "content": "Return {\"status\":\"ok\"}."},
            ],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "danish_rag_setup_probe",
                    "schema": schema,
                    "strict": True,
                },
            },
        }
        try:
            completion = self._request_openai_json(
                configuration.endpoint,
                "POST",
                "/v1/chat/completions",
                payload,
            )
            content = (
                completion.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            parsed = json.loads(content)
            if parsed != {"status": "ok"}:
                raise ValueError("response did not match the setup probe schema")
        except Exception as exc:
            return CapabilityTestResult(
                ok=False,
                reason="structured_output_failed",
                message=(
                    "The selected model did not return the required structured JSON. "
                    f"Choose a chat-capable local model and retry. Detail: {exc}"
                ),
            )

        return CapabilityTestResult(
            ok=True,
            reason="passed",
            message="Provider capability test passed.",
            provider_version=str(models_payload.get("object", "openai-compatible")),
            model_identity={"id": configuration.model},
            capabilities=["generation"],
        )

    def _request_openai_json(
        self,
        endpoint: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            require_loopback_endpoint(endpoint, purpose="Provider capability testing")
        except PrivacyBoundaryError as exc:
            raise RuntimeError(str(exc)) from exc
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            f"{endpoint.rstrip('/')}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def _openai_model_ids(payload: dict[str, Any]) -> set[str]:
    data = payload.get("data")
    if not isinstance(data, list):
        return set()
    model_ids = set()
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            model_ids.add(item["id"])
    return model_ids
