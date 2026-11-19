"""What the cache's callers hand it and get back (design §3)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from lite_model import Reply, Usage


@dataclass(frozen=True)
class Policy:
    """A call site's cache decision, passed as `call_context(cache=Policy(...))` (design §3).

    `ttl_s`: seconds an entry lives; None = until invalidated. `accept(reply)` returns None to
    store the reply or a reason to skip it (counted as `skip:<reason>`, §3.2). `refresh`: skip the
    read, call the model and overwrite (§3.3)."""

    ttl_s: int | None = None
    accept: Callable[[Reply], str | None] | None = None
    refresh: bool = False

    def __post_init__(self) -> None:
        if self.ttl_s is not None and self.ttl_s <= 0:
            raise ValueError(f"ttl_s must be positive or None, got {self.ttl_s}")


@dataclass(frozen=True)
class Entry:
    """One cached reply (design §4): the reply's value, its provenance and usage, opaque to the
    cache. `payload` is `{"text": ...}` for `text()` and the value's JSON for `structured()`."""

    key: str
    scope: str
    role: str
    backend: str
    model: str
    question: str | None
    kind: Literal["text", "structured"]
    payload: Any
    provenance: dict[str, Any]
    usage: Usage
    created_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True)
class RoleStats:
    """Counts for one role over the days and scopes asked for (design §3.4). `skips` maps each
    `accept` reason to its count."""

    role: str
    hits: int
    stored: int
    skips: dict[str, int]
    saved_input_tokens: int
    saved_output_tokens: int
    spent_input_tokens: int
    spent_output_tokens: int

    @property
    def hit_rate(self) -> float | None:
        """hits / every counted call; None when there were none."""
        calls = self.hits + self.stored + sum(self.skips.values())
        return self.hits / calls if calls else None
