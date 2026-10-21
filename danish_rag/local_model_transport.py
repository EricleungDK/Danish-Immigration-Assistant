"""Local model transport: the one module that owns HTTP access to local model providers.

Callers see a small operation interface (chat, embed, list models, version) and typed
errors. Loopback enforcement is built in and has no opt-out: every adapter checks its
endpoint on construction and again before every request, and never follows redirects.
Provider differences (Ollama vs OpenAI-compatible) live in adapters; deterministic
Ollama options and thinking-disabled are defined once, in the Ollama adapter.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Final, Mapping, Protocol, Sequence

from .privacy_boundary import require_loopback_endpoint


DEFAULT_OLLAMA_ENDPOINT: Final = "http://127.0.0.1:11434"

OLLAMA_DETERMINISTIC_CHAT_OPTIONS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "temperature": 0,
        "seed": 0,
    }
)
_OLLAMA_THINKING_ENABLED: Final = False


class LocalModelTransportError(RuntimeError):
    """Base class for transport failures. User-facing wording belongs to callers."""


class ModelUnreachableError(LocalModelTransportError):
    """The provider service could not be reached."""


class ModelTimeoutError(LocalModelTransportError):
    """The provider did not answer within the configured timeout."""


class ModelMissingError(LocalModelTransportError):
    """The requested model is not installed or loaded."""


class ModelProtocolError(LocalModelTransportError):
    """The provider answered, but not with a usable response."""


class ModelEmptyReplyError(ModelProtocolError):
    """The provider answered a chat request with no (or empty) message content."""


class UnsupportedOperationError(LocalModelTransportError):
    """The adapter's provider does not support this operation."""


@dataclass(frozen=True)
class ChatResult:
    content: str
    done_reason: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbedResult:
    """Embedding vectors plus the provider's raw response (e.g. Ollama durations)."""

    vectors: list[list[float]]
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelListing:
    """Lenient model listing: normalized entries plus the provider's raw response."""

    models: list[dict[str, Any]]
    raw: Mapping[str, Any] = field(default_factory=dict)


class LocalModelTransport(Protocol):
    endpoint: str

    def chat(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, str]],
        schema: Mapping[str, Any] | None = None,
        context_tokens: int | None = None,
    ) -> ChatResult: ...

    def embed(self, *, model: str, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_result(self, *, model: str, texts: Sequence[str]) -> EmbedResult: ...

    def list_models(self) -> list[dict[str, Any]]: ...

    def model_listing(self) -> ModelListing: ...

    def version(self) -> str: ...

    def show_model(self, model: str) -> dict[str, Any]: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


# No proxies (loopback traffic must not leave the machine) and no redirects.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


class _HttpJsonTransport:
    provider_name = "Provider"

    def __init__(self, endpoint: str, *, timeout_seconds: float = 60.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout_seconds = timeout_seconds
        require_loopback_endpoint(self.endpoint, purpose="Local model transport")

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        missing_statuses: Sequence[int] = (),
    ) -> dict[str, Any]:
        require_loopback_endpoint(self.endpoint, purpose="Local model transport")
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.endpoint}{path}", data=data, headers=headers, method=method
        )
        name = self.provider_name
        try:
            with _OPENER.open(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code in missing_statuses:
                raise ModelMissingError(
                    f"{name} does not have the requested model ({path})."
                ) from exc
            raise ModelProtocolError(f"{name} returned HTTP {exc.code} for {path}.") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ModelTimeoutError(f"{name} timed out at {self.endpoint}.") from exc
            raise ModelUnreachableError(f"{name} is unreachable at {self.endpoint}.") from exc
        except TimeoutError as exc:
            raise ModelTimeoutError(f"{name} timed out at {self.endpoint}.") from exc
        except (http.client.HTTPException, OSError) as exc:
            raise ModelUnreachableError(f"{name} is unreachable at {self.endpoint}.") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ModelProtocolError(
                f"{name} returned no usable JSON response for {path}."
            ) from exc
        if not isinstance(body, dict):
            raise ModelProtocolError(f"{name} response for {path} must be a JSON object.")
        return body

    def _unsupported(self, operation: str) -> UnsupportedOperationError:
        return UnsupportedOperationError(
            f"{self.provider_name} adapter does not support {operation}."
        )


class OllamaTransport(_HttpJsonTransport):
    """Ollama loopback HTTP adapter: chat, embed, list models, version, show model."""

    provider_name = "Ollama"

    def chat(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, str]],
        schema: Mapping[str, Any] | None = None,
        context_tokens: int | None = None,
    ) -> ChatResult:
        options = dict(OLLAMA_DETERMINISTIC_CHAT_OPTIONS)
        if context_tokens is not None:
            options["num_ctx"] = context_tokens
        payload: dict[str, Any] = {
            "model": model,
            "messages": [dict(message) for message in messages],
            "stream": False,
            "think": _OLLAMA_THINKING_ENABLED,
            "options": options,
        }
        if schema is not None:
            payload["format"] = dict(schema)
        body = self._request("POST", "/api/chat", payload, missing_statuses=(404,))
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content:
            raise ModelEmptyReplyError("chat response did not include message.content")
        done_reason = body.get("done_reason")
        return ChatResult(
            content=content,
            done_reason=done_reason if isinstance(done_reason, str) else None,
            raw=body,
        )

    def embed(self, *, model: str, texts: Sequence[str]) -> list[list[float]]:
        return self.embed_result(model=model, texts=texts).vectors

    def embed_result(self, *, model: str, texts: Sequence[str]) -> EmbedResult:
        body = self._request(
            "POST",
            "/api/embed",
            {"model": model, "input": list(texts)},
            missing_statuses=(404,),
        )
        vectors = body.get("embeddings")
        if vectors is None and "embedding" in body:
            vectors = [body["embedding"]]
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ModelProtocolError(
                f"Ollama embed response must contain {len(texts)} embedding(s)."
            )
        return EmbedResult(vectors=vectors, raw=body)

    def list_models(self) -> list[dict[str, Any]]:
        body = self._request("GET", "/api/tags")
        if not isinstance(body.get("models"), list):
            raise ModelProtocolError("Ollama /api/tags response did not include models.")
        return self._tag_entries(body)

    def model_listing(self) -> ModelListing:
        body = self._request("GET", "/api/tags")
        return ModelListing(models=self._tag_entries(body), raw=body)

    @staticmethod
    def _tag_entries(body: Mapping[str, Any]) -> list[dict[str, Any]]:
        models = body.get("models")
        entries: list[dict[str, Any]] = []
        for item in models if isinstance(models, list) else []:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model")
            if isinstance(name, str) and name:
                entries.append({**item, "name": name})
        return entries

    def version(self) -> str:
        return str(self._request("GET", "/api/version").get("version", ""))

    def show_model(self, model: str) -> dict[str, Any]:
        return self._request(
            "POST", "/api/show", {"model": model}, missing_statuses=(400, 404)
        )


class OpenAICompatibleTransport(_HttpJsonTransport):
    """OpenAI-compatible loopback adapter: chat and list models only."""

    provider_name = "OpenAI-compatible provider"

    def chat(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, str]],
        schema: Mapping[str, Any] | None = None,
        context_tokens: int | None = None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [dict(message) for message in messages],
            "stream": False,
            "temperature": 0,
        }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "danish_rag_response",
                    "schema": dict(schema),
                    "strict": True,
                },
            }
        body = self._request(
            "POST", "/v1/chat/completions", payload, missing_statuses=(404,)
        )
        choices = body.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content:
            raise ModelEmptyReplyError("chat response did not include message.content")
        finish_reason = first.get("finish_reason") if isinstance(first, dict) else None
        return ChatResult(
            content=content,
            done_reason=finish_reason if isinstance(finish_reason, str) else None,
            raw=body,
        )

    def embed(self, *, model: str, texts: Sequence[str]) -> list[list[float]]:
        raise self._unsupported("embed")

    def embed_result(self, *, model: str, texts: Sequence[str]) -> EmbedResult:
        raise self._unsupported("embed")

    def list_models(self) -> list[dict[str, Any]]:
        body = self._request("GET", "/v1/models")
        if not isinstance(body.get("data"), list):
            raise ModelProtocolError("OpenAI-compatible /v1/models response had no data.")
        return self._model_entries(body)

    def model_listing(self) -> ModelListing:
        body = self._request("GET", "/v1/models")
        return ModelListing(models=self._model_entries(body), raw=body)

    @staticmethod
    def _model_entries(body: Mapping[str, Any]) -> list[dict[str, Any]]:
        data = body.get("data")
        return [
            {**item, "name": item["id"]}
            for item in (data if isinstance(data, list) else [])
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
        ]

    def version(self) -> str:
        raise self._unsupported("version")

    def show_model(self, model: str) -> dict[str, Any]:
        raise self._unsupported("show_model")


# --- Scripted fake -------------------------------------------------------------------


class FakeTransportScriptError(AssertionError):
    """The fake was called out of script order, or after the script ran out."""


@dataclass(frozen=True)
class RecordedRequest:
    operation: str
    params: dict[str, Any]


class FakeTransport:
    """In-memory transport: records requests, replays queued results in order.

    The queued script is strict (out of order or exhausted fails loudly). With a
    `responder(operation, params) -> result`, a call made after the script has run out is
    answered by the responder instead, so a deterministic stand-in model can serve any
    number of requests; it returns the operation's natural result (a reply string, a list of
    vectors, ...) or a typed result, and may raise a transport error.
    """

    provider_name = "Fake"

    def __init__(
        self,
        endpoint: str = DEFAULT_OLLAMA_ENDPOINT,
        *,
        responder: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        require_loopback_endpoint(endpoint, purpose="Local model transport")
        self.endpoint = endpoint
        self.requests: list[RecordedRequest] = []
        self._script: list[tuple[str | None, Any]] = []
        self._responder = responder
        self._responder_calls = 0

    @property
    def remaining(self) -> int:
        return len(self._script)

    def assert_script_consumed(self, *, allow_responder: bool = True) -> None:
        """Fail unless every queued response was used (and, optionally, no call overflowed
        to the responder), so a test can pin exactly how many calls were made."""

        if self._script:
            raise FakeTransportScriptError(f"{len(self._script)} scripted response(s) unused")
        if not allow_responder and self._responder_calls:
            raise FakeTransportScriptError(
                f"{self._responder_calls} call(s) went past the script to the responder")

    def queue_chat(self, content: str, *, done_reason: str | None = None) -> None:
        self._script.append(("chat", ChatResult(content=content, done_reason=done_reason)))

    def queue_embed(
        self, vectors: list[list[float]], *, raw: Mapping[str, Any] | None = None
    ) -> None:
        self._script.append(("embed", EmbedResult(vectors=vectors, raw=dict(raw or {}))))

    def queue_models(
        self, models: list[dict[str, Any]], *, raw: Mapping[str, Any] | None = None
    ) -> None:
        listing = ModelListing(models=models, raw=dict(raw or {"object": "list"}))
        self._script.append(("list_models", listing))

    def queue_version(self, version: str) -> None:
        self._script.append(("version", version))

    def queue_show(self, payload: dict[str, Any]) -> None:
        self._script.append(("show_model", payload))

    def queue_error(self, error: Exception, *, operation: str | None = None) -> None:
        """Raise `error` for the next call (restricted to `operation` when given)."""
        self._script.append((operation, error))

    def chat(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, str]],
        schema: Mapping[str, Any] | None = None,
        context_tokens: int | None = None,
    ) -> ChatResult:
        return self._next(
            "chat",
            {
                "model": model,
                "messages": [dict(message) for message in messages],
                "schema": None if schema is None else dict(schema),
                "context_tokens": context_tokens,
            },
        )

    def embed(self, *, model: str, texts: Sequence[str]) -> list[list[float]]:
        return self.embed_result(model=model, texts=texts).vectors

    def embed_result(self, *, model: str, texts: Sequence[str]) -> EmbedResult:
        return self._next("embed", {"model": model, "texts": list(texts)})

    def list_models(self) -> list[dict[str, Any]]:
        return self.model_listing().models

    def model_listing(self) -> ModelListing:
        return self._next("list_models", {})

    def version(self) -> str:
        return self._next("version", {})

    def show_model(self, model: str) -> dict[str, Any]:
        return self._next("show_model", {"model": model})

    def _next(self, operation: str, params: dict[str, Any]) -> Any:
        self.requests.append(RecordedRequest(operation, params))
        if not self._script:
            if self._responder is None:
                raise FakeTransportScriptError(f"script exhausted; unexpected {operation}")
            self._responder_calls += 1
            return self._respond(operation, params)
        scripted_operation, result = self._script.pop(0)
        if scripted_operation is not None and scripted_operation != operation:
            raise FakeTransportScriptError(
                f"script expected {scripted_operation}, got {operation}"
            )
        if isinstance(result, Exception):
            raise result
        return result

    def _respond(self, operation: str, params: dict[str, Any]) -> Any:
        result = self._responder(operation, params)
        if operation == "chat" and isinstance(result, str):
            return ChatResult(content=result, done_reason=None)
        if operation == "embed":
            return result if isinstance(result, EmbedResult) else EmbedResult(
                vectors=result, raw={})
        if operation == "list_models" and not isinstance(result, ModelListing):
            return ModelListing(models=result, raw={"object": "list"})
        return result
