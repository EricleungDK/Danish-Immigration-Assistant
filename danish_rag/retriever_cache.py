"""One hybrid retriever per active knowledge release and embedding index (#79)."""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


def release_cache_key(active_release: dict[str, Any], index_metadata: dict[str, Any]) -> str:
    """Identity of the verified active release plus its embedding index.

    The active record carries the manifest and the index artifact hashes; the index
    metadata adds the embedding model and its identity, so a re-index of the same
    release changes the key.
    """

    identity = {
        "active": active_release,
        "embedding_model": index_metadata.get("embedding_model"),
        "embedding_model_identity": index_metadata.get("embedding_model_identity"),
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class RetrieverCache(Generic[T]):
    """Holds the latest built retriever; replaced only by a successful build."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entry: tuple[str, T] | None = None

    def get_or_build(
        self, key: str, build: Callable[[], T], key_of: Callable[[T], str]
    ) -> T:
        """Return the cached retriever for `key`, else build one under the lock.

        The entry is stored under `key_of(built)`, the identity of what was actually
        loaded (a release may activate between the key and the build).
        """

        with self._lock:
            if self._entry is not None and self._entry[0] == key:
                return self._entry[1]
            built = build()
            self._entry = (key_of(built), built)
            return built
