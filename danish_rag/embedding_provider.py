"""Local embedding-provider contract and production Ollama adapter."""

from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request
from typing import Any, Protocol

from .privacy_boundary import PrivacyBoundaryError, require_loopback_endpoint


DEFAULT_OLLAMA_EMBEDDING_ENDPOINT = "http://127.0.0.1:11434"


class EmbeddingProviderError(RuntimeError):
    """Raised when a local embedding provider cannot satisfy its contract."""


class EmbeddingProvider(Protocol):
    provider_id: str
    endpoint: str

    def inspect_model(self, model: str) -> dict[str, Any]: ...

    def embed(self, model: str, text: str) -> list[float]: ...


class OllamaEmbeddingProvider:
    """Production embedding adapter for Ollama's loopback HTTP API."""

    provider_id = "ollama"

    def __init__(
        self,
        endpoint: str = DEFAULT_OLLAMA_EMBEDDING_ENDPOINT,
        *,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout_seconds = timeout_seconds
        try:
            require_loopback_endpoint(self.endpoint, purpose="Local embedding")
        except PrivacyBoundaryError as exc:
            raise EmbeddingProviderError(
                "Local embedding requires an Ollama loopback endpoint such as "
                f"{DEFAULT_OLLAMA_EMBEDDING_ENDPOINT}. {exc}"
            ) from exc

    def inspect_model(self, model: str) -> dict[str, Any]:
        try:
            payload = self._request("POST", "/api/show", {"model": model})
        except _OllamaModelNotFoundError as exc:
            raise EmbeddingProviderError(
                f"The approved embedding model '{model}' is not installed in Ollama. "
                f"Run `ollama pull {model}` and retry local indexing."
            ) from exc
        except Exception as exc:
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
                tags_payload = self._request("GET", "/api/tags")
            except Exception:
                tags_payload = {}
            tag_identity = _matching_tag_identity(tags_payload, model)
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
            payload = self._request(
                "POST",
                "/api/embed",
                {"model": model, "input": text},
            )
        except Exception as exc:
            raise EmbeddingProviderError(
                f"Ollama could not embed text with '{model}' at {self.endpoint}. Start "
                f"Ollama, run `ollama pull {model}`, and retry. Detail: {exc}"
            ) from exc

        vector = _extract_embedding_vector(payload)
        return validate_embedding_vector(vector, context=f"Ollama model '{model}'")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
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
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if path == "/api/show" and exc.code in {400, 404}:
                raise _OllamaModelNotFoundError from exc
            raise EmbeddingProviderError(
                f"Ollama returned HTTP {exc.code} for {path}."
            ) from exc
        except urllib.error.URLError as exc:
            raise EmbeddingProviderError(
                f"Ollama is unreachable at {self.endpoint}."
            ) from exc
        except (TimeoutError, json.JSONDecodeError) as exc:
            raise EmbeddingProviderError(
                f"Ollama returned no usable JSON response for {path}."
            ) from exc
        if not isinstance(response_payload, dict):
            raise EmbeddingProviderError(
                f"Ollama response for {path} must be a JSON object."
            )
        return response_payload


class _OllamaModelNotFoundError(RuntimeError):
    pass


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


def _extract_embedding_vector(payload: dict[str, Any]) -> Any:
    if "embedding" in payload:
        return payload["embedding"]
    embeddings = payload.get("embeddings")
    if isinstance(embeddings, list) and embeddings:
        first = embeddings[0]
        if isinstance(first, dict) and "embedding" in first:
            return first["embedding"]
        return first
    return None


def _matching_tag_identity(
    payload: dict[str, Any],
    requested_model: str,
) -> dict[str, Any] | None:
    models = payload.get("models")
    if not isinstance(models, list):
        return None
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
