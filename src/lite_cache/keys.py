"""The L1 key (design §3.1) and what a call's context says about caching (§3)."""

import re
from collections.abc import Mapping
from typing import Any

from lite_model.hooks import Call

from lite_cache.canonical import content_hash
from lite_cache.errors import InvalidScope
from lite_cache.types import Policy

# Bump when the key's content or the canonical rules change: every old key stops matching (§3.1).
KEY_VERSION = 2

# One or more `/`-separated segments, coarse to fine (project, tenant / namespace, kb, user...).
SCOPE = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")


def check_scope(scope: object) -> str:
    """`scope` if it is one or more `/`-separated `[A-Za-z0-9_.-]+` segments; else InvalidScope."""
    if not isinstance(scope, str) or not SCOPE.fullmatch(scope):
        raise InvalidScope(
            f"scope must be `/`-separated segments of [A-Za-z0-9_.-]+, got {scope!r}"
        )
    return scope


def policy_from(context: Mapping[str, Any]) -> Policy | None:
    """The cache policy of a call: `cache=Policy(...)`, or `True` for `Policy()`; None (absent or
    False) means do not cache. Anything else is a caller's mistake (§3)."""
    value = context.get("cache")
    if value is None or value is False:
        return None
    if value is True:
        return Policy()
    if isinstance(value, Policy):
        return value
    raise TypeError(f"call_context(cache=...) takes a lite_cache.Policy or True, got {value!r}")


def cache_key(call: Call, scope: str) -> str:
    """sha256 of the canonical JSON of what decides the answer: scope, role, the configured
    backend and the requested model, the messages and the request parameters as sent (§3.1).
    The caller's `question` is not part of it."""
    return content_hash(
        {
            "v": KEY_VERSION,
            "scope": scope,
            "role": call.role,
            "backend": call.target.backend,
            "model": call.target.model,
            "messages": [[m.role, m.content] for m in call.messages],
            "params": dict(call.params),
        }
    )
