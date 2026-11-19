"""lite-cache: an LLM response cache plugged into lite-model as a hook.

Design: docs/lite-cache-design.md."""

from lite_cache.errors import InvalidScope, LiteCacheError, StoreError

__all__ = ["InvalidScope", "LiteCacheError", "StoreError"]
