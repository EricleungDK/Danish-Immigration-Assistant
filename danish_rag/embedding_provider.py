"""Local embedding-provider contract and production Ollama adapter."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Protocol

from .local_model_transport import (
    LocalModelTransport,
    LocalModelTransportError,
    ModelMissingError,
    OllamaTransport,
)
from .privacy_boundary import PrivacyBoundaryError


DEFAULT_OLLAMA_EMBEDDING_ENDPOINT = "http://127.0.0.1:11434"


class EmbeddingProviderError(RuntimeError):
    """Raised when a local embedding provider cannot satisfy its contract."""


class EmbeddingProvider(Protocol):
    provider_id: str
    endpoint: str

    def inspect_model(self, model: str) -> dict[str, Any]: ...

    def embed(self, model: str, text: str) -> list[float]: ...


class OllamaEmbeddingProvider:
    """Production embedding adapter over the local model transport (Ollama, loopback)."""

    provider_id = "ollama"

    def __init__(
        self,
        endpoint: str = DEFAULT_OLLAMA_EMBEDDING_ENDPOINT,
        *,
        timeout_seconds: float = 60.0,
        transport: LocalModelTransport | None = None,
    ) -> None:
        if transport is None:
            try:
                transport = OllamaTransport(endpoint, timeout_seconds=timeout_seconds)
            except PrivacyBoundaryError as exc:
                raise EmbeddingProviderError(
                    "Local embedding requires an Ollama loopback endpoint such as "
                    f"{DEFAULT_OLLAMA_EMBEDDING_ENDPOINT}. {exc}"
                ) from exc
        self._transport = transport
        self.endpoint = transport.endpoint
        self.timeout_seconds = timeout_seconds

    def inspect_model(self, model: str) -> dict[str, Any]:
        try:
            payload = self._transport.show_model(model)
        except ModelMissingError as exc:
            raise EmbeddingProviderError(
                f"The approved embedding model '{model}' is not installed in Ollama. "
                f"Run `ollama pull {model}` and retry local indexing."
            ) from exc
        except LocalModelTransportError as exc:
            raise EmbeddingProviderError(
                f"Could not inspect the approved embedding model '{model}' at the local "
                f"Ollama endpoint {self.endpoint}. Start Ollama, run `ollama pull {model}`, "
                f"and retry. Detail: {exc}"
            ) from exc

        identity: dict[str, Any] = {
            "model": payload.get("model") or payload.get("name") or model,
        }
        for key in ("details", "model_info", "modified_at", "digest", "capabilities"):
            if key in payload:
                identity[key] = payload[key]

        if not identity.get("digest"):
            try:
                installed = self._transport.list_models()
            except LocalModelTransportError:
                installed = []
            tag_identity = _matching_tag_identity(installed, model)
            if tag_identity is not None:
                identity["model"] = tag_identity.get("model") or tag_identity.get("name") or model
                for key in ("digest", "modified_at", "details"):
                    if key in tag_identity and key not in identity:
                        identity[key] = tag_identity[key]
                    elif key == "digest" and key in tag_identity:
                        identity[key] = tag_identity[key]

        return canonical_model_identity(identity)

    def embed(self, model: str, text: str) -> list[float]:
        try:
            vectors = self._transport.embed(model=model, texts=[text])
        except LocalModelTransportError as exc:
            raise EmbeddingProviderError(
                f"Ollama could not embed text with '{model}' at {self.endpoint}. Start "
                f"Ollama, run `ollama pull {model}`, and retry. Detail: {exc}"
            ) from exc
        return validate_embedding_vector(vectors[0] if vectors else None, context=f"Ollama model '{model}'")


def resolve_embedding_provider(
    provider: EmbeddingProvider | None,
    *,
    endpoint: str | None = None,
) -> EmbeddingProvider:
    if provider is not None:
        return provider
    return OllamaEmbeddingProvider(endpoint or DEFAULT_OLLAMA_EMBEDDING_ENDPOINT)


def canonical_model_identity(identity: dict[str, Any]) -> dict[str, Any]:
    """Return JSON-stable model evidence with a compatibility fingerprint."""

    if not isinstance(identity, dict) or not identity:
        raise EmbeddingProviderError(
            "Embedding model inspection returned no model identity. Reinstall the approved "
            "model and retry indexing."
        )
    stable_identity = json.loads(json.dumps(identity, sort_keys=True))
    stable_identity.pop("identity_fingerprint_sha256", None)
    fingerprint_input = json.dumps(
        stable_identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    stable_identity["identity_fingerprint_sha256"] = hashlib.sha256(
        fingerprint_input
    ).hexdigest()
    return stable_identity


def embedding_provider_id(provider: EmbeddingProvider) -> str:
    provider_id = str(getattr(provider, "provider_id", "")).strip()
    if not provider_id:
        raise EmbeddingProviderError(
            "Embedding provider did not declare a stable provider_id."
        )
    return provider_id


def validate_embedding_vector(value: Any, *, context: str) -> list[float]:
    if not isinstance(value, list) or not value:
        raise EmbeddingProviderError(
            f"{context} returned an invalid embedding vector; expected a non-empty list."
        )
    vector: list[float] = []
    for item in value:
        if (
            not isinstance(item, int | float)
            or isinstance(item, bool)
            or not math.isfinite(item)
        ):
            raise EmbeddingProviderError(
                f"{context} returned an invalid embedding vector; every dimension must "
                "be a finite number."
            )
        vector.append(float(item))
    return vector


def _matching_tag_identity(
    models: list[dict[str, Any]],
    requested_model: str,
) -> dict[str, Any] | None:
    requested_reference = _normalized_model_reference(requested_model)
    for candidate in models:
        if not isinstance(candidate, dict):
            continue
        names = {str(candidate.get("name", "")), str(candidate.get("model", ""))}
        if requested_reference in {_normalized_model_reference(name) for name in names if name}:
            return candidate
    return None


def _normalized_model_reference(model: str) -> str:
    reference = model.strip()
    final_segment = reference.rsplit("/", 1)[-1]
    if ":" not in final_segment:
        return f"{reference}:latest"
    return reference
