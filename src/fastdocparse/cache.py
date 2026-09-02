"""Pluggable caching for DocumentParser.extract(). Off by default — pass a cache instance to opt in.

Caching is skipped whenever custom `rules` are passed to extract(), since a rule is an
arbitrary callable that can't be safely fingerprinted — caching would risk returning a
result validated under a different rule than the one just requested.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any, Callable, Protocol

from .config import ExtractionConfig
from .schema import Schema


class Cache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def set(self, key: str, value: dict[str, Any]) -> None: ...


class InMemoryCache:
    """Process-local cache. Good for a long-running batch job or server process; lost on restart.

    Pass max_size to cap memory use — oldest-accessed entries are evicted once the cap is
    hit (LRU). Leave it None (default) only for short-lived scripts/batch jobs where the
    process exits before the cache could grow unbounded.
    """

    def __init__(self, max_size: int | None = None):
        if max_size is not None and max_size <= 0:
            raise ValueError(f"max_size must be positive, got {max_size}")
        self._max_size = max_size
        self._store: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def get(self, key: str) -> dict[str, Any] | None:
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._store[key] = value
        self._store.move_to_end(key)
        if self._max_size is not None:
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    def __len__(self) -> int:
        return len(self._store)


def make_cache_key(
    document_bytes: bytes,
    schema: Schema,
    kind: str,
    config: ExtractionConfig,
    handler: Callable[..., str],
) -> str:
    """Fingerprint a (document, schema, ingestion kind, config, handler) combination.

    handler is included, not just kind, because handlers are registered per
    DocumentParser instance — two instances can register different handlers under the
    same kind name, and a cache shared between them must not conflate the two.
    Identity is process-local (qualified name + id()), which matches InMemoryCache's own
    process-local lifetime — it isn't meant to survive a restart anyway.
    """
    document_digest = hashlib.sha256(document_bytes).hexdigest()
    schema_digest = hashlib.sha256(schema.model_dump_json().encode()).hexdigest()
    config_digest = hashlib.sha256(json.dumps(config.__dict__, sort_keys=True).encode()).hexdigest()
    handler_identity = f"{getattr(handler, '__module__', '?')}.{getattr(handler, '__qualname__', repr(handler))}:{id(handler)}"
    return hashlib.sha256(f"{document_digest}:{schema_digest}:{kind}:{config_digest}:{handler_identity}".encode()).hexdigest()
