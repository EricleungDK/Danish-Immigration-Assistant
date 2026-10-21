"""Compatibility re-export: the Ollama contract is owned by the Ollama transport adapter."""

from __future__ import annotations

from .local_model_transport import OLLAMA_DETERMINISTIC_CHAT_OPTIONS

__all__ = ["OLLAMA_DETERMINISTIC_CHAT_OPTIONS"]
