"""Provider setup, capability testing, and local configuration persistence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .local_model_transport import (
    LocalModelTransport,
    LocalModelTransportError,
    OllamaTransport,
    OpenAICompatibleTransport,
)
from .privacy_boundary import PrivacyBoundaryError, is_loopback_url
from .runtime_policy import load_runtime_policy
from .runtime_probe import ProbeExitStatus, run_runtime_probe


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = ROOT / "config" / "runtime-policy.json"

TransportFactory = Callable[[str, str, float], LocalModelTransport]
_TRANSPORT_ERRORS = (LocalModelTransportError, PrivacyBoundaryError)


def default_transport_factory(
    provider_id: str, endpoint: str, timeout_seconds: float
) -> LocalModelTransport:
    if provider_id == "ollama":
        return OllamaTransport(endpoint, timeout_seconds=timeout_seconds)
    if provider_id == "openai_compatible":
        return OpenAICompatibleTransport(endpoint, timeout_seconds=timeout_seconds)
    raise ValueError(f"Unsupported provider: {provider_id}")


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


@dataclass(frozen=True)
class ModelDiscoveryResult:
    ok: bool
    reason: str
    message: str
    models: list[str] = field(default_factory=list)

    @classmethod
    def from_value(
        cls,
        value: "ModelDiscoveryResult | dict[str, Any]",
    ) -> "ModelDiscoveryResult":
        if isinstance(value, cls):
            return value
        return cls(
            ok=bool(value.get("ok")),
            reason=str(value.get("reason", "")),
            message=str(value.get("message", "")),
            models=[str(item) for item in value.get("models", [])],
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


class ProviderModelDiscoverer:
    def __init__(
        self,
        *,
        policy_path: str | Path = DEFAULT_POLICY_PATH,
        timeout_seconds: float = 10.0,
        transport_factory: TransportFactory = default_transport_factory,
    ) -> None:
        self.policy = load_runtime_policy(policy_path)
        self.timeout_seconds = timeout_seconds
        self._transport_factory = transport_factory

    def __call__(self, configuration: ProviderConfiguration) -> ModelDiscoveryResult:
        failures = validate_provider_configuration(
            ProviderConfiguration(
                provider_id=configuration.provider_id,
                endpoint=configuration.endpoint,
                model="discovery-placeholder",
            )
        )
        if failures:
            return ModelDiscoveryResult(
                ok=False,
                reason="invalid_configuration",
                message=" ".join(failures),
            )

        if configuration.provider_id not in PROVIDERS:
            return ModelDiscoveryResult(
                ok=False,
                reason="unsupported_provider",
                message="Choose one of the supported local generation-model providers.",
            )

        try:
            transport = self._transport_factory(
                configuration.provider_id, configuration.endpoint, self.timeout_seconds
            )
            if configuration.provider_id == "ollama":
                models = self._discover_ollama(transport)
            else:
                models = self._discover_openai_compatible(transport)
        except _TRANSPORT_ERRORS as exc:
            return ModelDiscoveryResult(
                ok=False,
                reason="service_unreachable",
                message=(
                    "Start the selected local provider, confirm its loopback endpoint "
                    f"is reachable, and retry model discovery. Detail: {exc}"
                ),
            )

        if not models:
            return ModelDiscoveryResult(
                ok=False,
                reason="no_compatible_models",
                message=(
                    "No compatible local generation models were found. Install or load "
                    "a local chat model that supports the provider generation contract, "
                    "then retry."
                ),
            )
        return ModelDiscoveryResult(
            ok=True,
            reason="passed",
            message="Compatible local generation models found.",
            models=models,
        )

    def _discover_ollama(self, transport: LocalModelTransport) -> list[str]:
        identity = self.policy["models"]["generation"].get("identity", {})
        compatible: list[str] = []
        for entry in transport.model_listing().models[:100]:
            model = entry["name"]
            if _is_cloud_model_id(model):
                continue
            try:
                payload = transport.show_model(model)
            except _TRANSPORT_ERRORS:
                continue
            if _ollama_model_matches_generation_contract(payload, identity):
                compatible.append(model)
        return sorted(set(compatible), key=str.casefold)

    def _discover_openai_compatible(self, transport: LocalModelTransport) -> list[str]:
        return sorted(
            (
                entry["name"]
                for entry in transport.model_listing().models
                if not _is_cloud_model_id(entry["name"])
            ),
            key=str.casefold,
        )


class ProviderCapabilityTester:
    def __init__(
        self,
        *,
        policy_path: str | Path = DEFAULT_POLICY_PATH,
        timeout_seconds: float = 60.0,
        transport_factory: TransportFactory | None = None,
    ) -> None:
        self.policy = load_runtime_policy(policy_path)
        self.timeout_seconds = timeout_seconds
        self._transport_factory = transport_factory

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
        # Without an injected factory the probe builds its own loopback Ollama
        # transport and reports a refused or unreachable endpoint itself.
        transport = (
            self._transport_factory("ollama", configuration.endpoint, self.timeout_seconds)
            if self._transport_factory
            else None
        )
        result = run_runtime_probe(
            policy, transport=transport, timeout_seconds=self.timeout_seconds
        )
        if result.exit_status is ProbeExitStatus.PASSED:
            return CapabilityTestResult(
                ok=True,
                reason="passed",
                message="Provider capability test passed.",
                provider_version=str(result.provider.get("version", "")),
                model_identity=dict(result.model.get("identity") or {}),
                capabilities=[str(item) for item in result.model.get("capabilities", [])],
            )

        reason_by_exit_status = {
            ProbeExitStatus.PROVIDER_UNREACHABLE: "service_unreachable",
            ProbeExitStatus.PROVIDER_VERSION_UNSUPPORTED: "incompatible_provider_version",
            ProbeExitStatus.MODEL_UNAVAILABLE: "model_unavailable",
            ProbeExitStatus.STRUCTURED_RESPONSE_INVALID: "structured_output_failed",
            ProbeExitStatus.NON_LOOPBACK_ENDPOINT: "invalid_endpoint",
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
        factory = self._transport_factory or default_transport_factory
        try:
            transport = factory(
                configuration.provider_id, configuration.endpoint, self.timeout_seconds
            )
            listing = transport.model_listing()
            model_ids = {entry["name"] for entry in listing.models}
        except _TRANSPORT_ERRORS as exc:
            return CapabilityTestResult(
                ok=False,
                reason="service_unreachable",
                message=(
                    "Provider service is unreachable. Start the local server, confirm "
                    f"{configuration.endpoint}/v1/models responds, and retry. Detail: {exc}"
                ),
            )

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
        messages = [
            {"role": "system", "content": "Return only JSON."},
            {"role": "user", "content": 'Return {"status":"ok"}.'},
        ]
        try:
            completion = transport.chat(
                model=configuration.model, messages=messages, schema=schema
            )
            parsed = json.loads(completion.content)
            if parsed != {"status": "ok"}:
                raise ValueError("response did not match the setup probe schema")
        except (*_TRANSPORT_ERRORS, ValueError) as exc:
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
            provider_version=str(listing.raw.get("object", "openai-compatible")),
            model_identity={"id": configuration.model},
            capabilities=["generation"],
        )


def _is_cloud_model_id(model: str) -> bool:
    return model.casefold().endswith(":cloud")


def _ollama_model_matches_generation_contract(
    payload: dict[str, Any],
    identity: dict[str, Any],
) -> bool:
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list) or "completion" not in capabilities:
        return False
    details = payload.get("details")
    model_info = payload.get("model_info")
    if not isinstance(details, dict) or not isinstance(model_info, dict):
        return False
    family = details.get("family")
    if not family:
        families = details.get("families")
        if isinstance(families, list):
            family = next(
                (candidate for candidate in families if isinstance(candidate, str)),
                None,
            )
    expected = {
        "family": identity.get("family"),
        "architecture": identity.get("architecture"),
        "quantization_level": identity.get("quantization_level"),
    }
    actual = {
        "family": family,
        "architecture": model_info.get("general.architecture"),
        "quantization_level": details.get("quantization_level"),
    }
    return all(
        not expected_value or actual[key] == expected_value
        for key, expected_value in expected.items()
    )
