"""`CacheHook`: lite-cache plugged into lite-model (design §2, §3, §3.5).

`before(call)` answers a repeated request from the store; `after(call, reply)` stores a fresh
one. A call is cached only when its `call_context` has both a `scope` and a `cache` policy."""

import dataclasses
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from lite_model import Provenance, Reply, StructuredReply, TextReply
from lite_model.hooks import Call
from pydantic import ValidationError

from lite_cache.keys import cache_key, check_scope, policy_from
from lite_cache.store import Store
from lite_cache.types import Entry, Policy, RoleStats

log = logging.getLogger("lite_cache")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CacheHook:
    """A lite-model `Hook` named "lite-cache". A read failure raises (no request is sent); a
    write or count failure after a paid call never does: it is logged and counted in
    `store_errors`, and the reply is returned (§3.5)."""

    name = "lite-cache"

    def __init__(self, store: Store, clock: Callable[[], datetime] = _utcnow) -> None:
        self._store = store
        self._clock = clock
        self.store_errors = 0

    def before(self, call: Call) -> Reply | None:
        cached = _cached(call)
        if cached is None or cached[0].refresh:
            return None
        scope = cached[1]
        now = self._clock()
        entry = self._store.get(cache_key(call, scope), now)  # StoreError: the call fails unsent
        if entry is None:
            return None
        reply = _restore(call, entry)
        if reply is None:
            return None
        self._safely(
            "count a hit",
            lambda: self._store.count(now.date(), scope, call.role, "hit", saved=entry.usage),
        )
        return reply

    def after(self, call: Call, reply: Reply) -> None:
        cached = _cached(call)
        if cached is None:
            return
        policy, scope = cached
        now = self._clock()

        def store() -> None:
            key = cache_key(call, scope)  # inside: a request that cannot be keyed loses no reply
            reason = policy.accept(reply) if policy.accept is not None else None
            if reason is not None:
                outcome = f"skip:{reason}"
                self._store.count(now.date(), scope, call.role, outcome, spent=reply.usage)
                return
            entry = _entry(call, reply, key, scope, policy, now)
            self._store.put_and_count(entry, now, now.date(), spent=reply.usage)

        self._safely("store a reply", store)

    def invalidate(
        self, *, scope: str | None = None, role: str | None = None, key: str | None = None
    ) -> int:
        """Delete matching entries (§3.3); see `Store.invalidate`."""
        return self._store.invalidate(scope=scope, role=role, key=key)

    def stats(self, *, scope: str | None = None, since: date | None = None) -> list[RoleStats]:
        """Counts per role (§3.4); see `Store.stats`."""
        return self._store.stats(scope=scope, since=since)

    def _safely(self, what: str, action: Callable[[], None]) -> None:
        """Run a write that must not fail the call: a paid reply is never dropped (§3.5)."""
        try:
            action()
        except Exception as exc:
            self.store_errors += 1
            log.warning("lite-cache could not %s: %s: %s", what, type(exc).__name__, exc)


def _cached(call: Call) -> tuple[Policy, str] | None:
    """(policy, scope) when the call is to be cached; None without a policy or a scope."""
    policy = policy_from(call.context)
    if policy is None or "scope" not in call.context:
        return None
    return policy, check_scope(call.context["scope"])


def _entry(call: Call, reply: Reply, key: str, scope: str, policy: Policy, now: datetime) -> Entry:
    if isinstance(reply, TextReply):
        kind, payload = "text", {"text": reply.text}
    else:
        kind, payload = "structured", reply.value.model_dump(mode="json", by_alias=True)
    provenance = {f.name: getattr(reply.provenance, f.name) for f in dataclasses.fields(Provenance)}
    provenance["via"] = dict(provenance["via"])
    question = call.context.get("question")
    return Entry(
        key=key,
        scope=scope,
        role=call.role,
        backend=call.target.backend,
        model=call.target.model,
        question=question if isinstance(question, str) else None,
        kind=kind,  # type: ignore[arg-type]
        payload=payload,
        provenance=provenance,
        usage=reply.usage,
        created_at=now,
        expires_at=None if policy.ttl_s is None else now + timedelta(seconds=policy.ttl_s),
    )


def _restore(call: Call, entry: Entry) -> Reply | None:
    """The stored reply as the call's reply type; None (a miss, logged) when it no longer fits:
    the schema's validators changed while its JSON schema (and so the key) did not, or the row
    was written by a version whose provenance or payload had another shape."""
    try:
        provenance = Provenance(**entry.provenance)
        if call.schema is None:
            return (
                TextReply(entry.payload["text"], provenance, entry.usage)
                if entry.kind == "text"
                else None
            )
        if entry.kind != "structured":
            return None
        value = call.schema.model_validate(entry.payload)
    except ValidationError as exc:
        log.warning(
            "lite-cache: a cached %s no longer validates (%s errors); calling the model again",
            call.schema.__name__ if call.schema else "reply",
            exc.error_count(),
        )
        return None
    except (TypeError, KeyError) as exc:
        log.warning(
            "lite-cache could not restore entry %s (%s: %s); calling the model again",
            entry.key[:12],
            type(exc).__name__,
            exc,
        )
        return None
    return StructuredReply(value, provenance, entry.usage)
