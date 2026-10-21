"""One hybrid retriever per active knowledge release and embedding index (#79)."""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


def release_cache_key(
    active_release: dict[str, Any],
    index_metadata: dict[str, Any],
    live_embedding_identity: dict[str, Any] | None,
) -> str:
    """Identity of the verified active release, its embedding index and the live model.

    The active record carries the manifest and the index artifact hashes; the index
    metadata adds the embedding model and its identity, so a re-index of the same
    release changes the key. The live provider identity is part of the key so a swapped
    embedding model is rebuilt (and rejected as before) instead of served from cache.
    """

    identity = {
        "active": active_release,
        "embedding_model": index_metadata.get("embedding_model"),
        "embedding_model_identity": index_metadata.get("embedding_model_identity"),
        "live_embedding_identity": live_embedding_identity,
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class RetrieverCache(Generic[T]):
    """Holds the latest built retriever; replaced only by a successful build."""

    def __init__(self) -> None:
        self._lock = threading.Lock()  # short: entry lookup and store only
        self._build_lock = threading.Lock()  # serializes builds; hits never take it
        self._entry: tuple[str, T] | None = None

    def _lookup(self, key: str) -> T | None:
        with self._lock:
            if self._entry is not None and self._entry[0] == key:
                return self._entry[1]
        return None

    def get_or_build(
        self, key: str, build: Callable[[], T], key_of: Callable[[T], str]
    ) -> T:
        """Return the cached retriever for `key`, else build one (once) and store it.

        The entry is stored under `key_of(built)`, the identity of what was actually
        loaded (a release may activate between the key and the build). A failed build
        leaves the previous entry in place.
        """

        hit = self._lookup(key)
        if hit is not None:
            return hit
        with self._build_lock:
            hit = self._lookup(key)  # another thread may have built it meanwhile
            if hit is not None:
                return hit
            built = build()
            with self._lock:
                self._entry = (key_of(built), built)
            return built
