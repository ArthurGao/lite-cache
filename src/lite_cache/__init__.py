"""lite-cache: an LLM response cache plugged into lite-model as a hook.

Design: docs/lite-cache-design.md."""

from lite_cache.config import Settings
from lite_cache.errors import InvalidScope, LiteCacheError, StoreError
from lite_cache.hook import CacheHook
from lite_cache.store import PgStore, Store, open_store
from lite_cache.types import Policy, RoleStats

__all__ = [
    "CacheHook",
    "InvalidScope",
    "LiteCacheError",
    "PgStore",
    "Policy",
    "RoleStats",
    "Settings",
    "Store",
    "StoreError",
    "open_store",
]
