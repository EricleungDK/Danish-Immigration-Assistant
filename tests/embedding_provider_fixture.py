from __future__ import annotations

import hashlib
import math
import re
from typing import Any


TOKEN_PATTERN = re.compile(r"[0-9a-zA-ZæøåÆØÅ]+")


class DeterministicEmbeddingProviderFixture:
    """Explicit test-only embedding provider with embeddinggemma-compatible dimensions."""

    provider_id = "deterministic-embedding-provider-fixture"
    endpoint = "fixture://deterministic-embedding-provider"

    def __init__(
        self,
        *,
        vector_dimensions: int = 768,
        model_identity: dict[str, Any] | None = None,
        fail_on_embed: bool = False,
    ) -> None:
        self.vector_dimensions = vector_dimensions
        self._model_identity = model_identity or {
            "model": "embeddinggemma",
            "digest": "sha256:deterministic-embedding-provider-fixture",
            "details": {
                "family": "gemma3",
                "parameter_size": "fixture",
            },
        }
        self.fail_on_embed = fail_on_embed
        self.inspected_models: list[str] = []
        self.embedding_calls: list[dict[str, str]] = []

    def inspect_model(self, model: str) -> dict[str, Any]:
        self.inspected_models.append(model)
        return dict(self._model_identity)

    def embed(self, model: str, text: str) -> list[float]:
        self.embedding_calls.append({"model": model, "text": text})
        if self.fail_on_embed:
            raise OSError("simulated fixture embedding outage")

        vector = [0.0] * self.vector_dimensions
        for token in TOKEN_PATTERN.findall(text.casefold()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.vector_dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [round(value / norm, 9) for value in vector]
