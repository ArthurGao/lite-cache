"""lite-cache: an LLM response cache plugged into lite-model as a hook.

Design: docs/lite-cache-design.md."""

from lite_cache.config import Settings
from lite_cache.errors import (
    ConfigError,
    EmbedderError,
    InvalidScope,
    LiteCacheError,
    StoreError,
    VerifierError,
)
from lite_cache.hook import AsyncCacheHook, CacheHook
from lite_cache.semantic import Embedder, Verifier
from lite_cache.store import PgStore, Store, open_store
from lite_cache.types import Policy, RoleStats
from lite_cache.verifier import ModelVerifier

__all__ = [
    "AsyncCacheHook",
    "CacheHook",
    "ConfigError",
    "Embedder",
    "EmbedderError",
    "InvalidScope",
    "LiteCacheError",
    "ModelVerifier",
    "PgStore",
    "Policy",
    "RoleStats",
    "Settings",
    "Store",
    "StoreError",
    "Verifier",
    "VerifierError",
    "open_store",
]
