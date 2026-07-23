"""Shared request contracts for local Ollama adapters."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping


OLLAMA_DETERMINISTIC_CHAT_OPTIONS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "temperature": 0,
        "seed": 0,
    }
)
