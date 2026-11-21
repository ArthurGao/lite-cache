"""`CacheHook`: lite-cache plugged into lite-model (design §2, §3, §3.5).

`before(call)` answers a repeated request from the store; `after(call, reply)` stores a fresh
one. A call is cached only when its `call_context` has both a `scope` and a `cache` policy.
With `Policy(semantic=True)`, a `question` and a `partition`, an L1 miss also looks for a
similar question in that scope and partition (L2, §2 step 4)."""

import asyncio
import dataclasses
import logging
import math
import threading
import weakref
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta

from lite_model import Provenance, Reply, StructuredReply, TextReply, Usage, call_context
from lite_model.hooks import Call
from pydantic import ValidationError

from lite_cache import semantic
from lite_cache.errors import ConfigError, EmbedderError, VerifierError
from lite_cache.keys import cache_key, check_scope, policy_from
from lite_cache.semantic import Embedder, Verifier, guard
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

    def __init__(
        self,
        store: Store,
        clock: Callable[[], datetime] = _utcnow,
        *,
        embedder: Embedder | None = None,
        verifier: Verifier | None = None,
        thresholds: Mapping[str, float] | None = None,
    ) -> None:
        """`embedder` (L2 only) and `verifier` (optional, design §13) are the caller's; the
        threshold of that pair (`semantic.threshold_key`) must exist in `thresholds` (default: the
        calibrated table), or construction fails (design §0)."""
        self._store = store
        self._clock = clock
        self._embedder = embedder
        self._verifier = verifier
        self._threshold: float | None = None
        if embedder is not None:
            table = dict(thresholds) if thresholds is not None else semantic.thresholds()
            key = semantic.threshold_key(embedder, verifier)
            self._threshold = table.get(key)
            if self._threshold is None:
                raise ConfigError(
                    f"no calibrated L2 threshold for {key!r} "
                    "(design §8, §13; LITECACHE_SEMANTIC_THRESHOLDS)"
                )
        # The vector a lookup made, kept for the write of the same call (no second embedding).
        # Weak references: a call that fails never reaches `after`, and must not be kept alive.
        self._vectors: OrderedDict[int, tuple[weakref.ref[Call], list[float]]] = OrderedDict()
        # One hook serves calls from several threads (Bounded.map's workers, AsyncCacheHook's
        # to_thread): the vector map and the counters change under this lock. Reentrant: the
        # weakref callback `forget` can run during a garbage collection inside a locked block.
        self._lock = threading.RLock()
        self.store_errors = 0
        self.verifier_rejects = 0  # L2 candidates the verifier judged different (design §13)

    def before(self, call: Call) -> Reply | None:
        cached = _cached(call)
        if cached is None:
            return None
        policy, scope = cached
        if _semantic(call, policy) is not None and self._embedder is None:
            # Before the L1 lookup and the refresh check, so it shows on every such call.
            raise ConfigError("Policy(semantic=True) needs CacheHook(embedder=...)")
        if policy.refresh:
            return None
        now = self._clock()
        entry = self._store.get(cache_key(call, scope), now)  # StoreError: the call fails unsent
        if entry is None:
            return self._similar(call, policy, scope, now)
        reply = _restore(call, entry)
        if reply is None:
            return self._similar(call, policy, scope, now)
        self._count_hit(call, scope, "hit", entry, now)
        return reply

    def _similar(self, call: Call, policy: Policy, scope: str, now: datetime) -> Reply | None:
        """L2: the reply to a similar question in the same scope and partition, when the
        similarity reaches the embedder's calibrated threshold and the guard sees no difference."""
        wanted = _semantic(call, policy)
        if wanted is None or self._embedder is None or self._threshold is None:
            return None
        question, partition = wanted
        vector = _embed(self._embedder, question)
        self._remember(call, vector)
        found = self._store.nearest(
            scope,
            partition,
            self._embedder.model_id,
            vector,
            now,
            role=call.role,
            kind="text" if call.schema is None else "structured",
        )
        if found is None:
            return None
        entry, similarity = found
        # `not >=`: a NaN similarity is a miss, never a hit
        if not similarity >= self._threshold or entry.question is None:
            return None
        if guard(question, entry.question) is not None:
            return None
        if not self._verified(question, entry.question, entry.key):
            return None
        reply = _restore(call, entry)
        if reply is not None:
            self._take_vector(call)  # a hit: after() will not run for this call
            self._count_hit(call, scope, "hit_semantic", entry, now)
        return reply

    def _verified(self, question: str, candidate: str, key: str) -> bool:
        """True without a verifier; otherwise its verdict. A failure is a read failure. Any model
        call the verifier makes runs without this call's policy and question: it is not cached
        and cannot recurse into L2 (design §13), whatever the verifier."""
        if self._verifier is None:
            return True
        try:
            with call_context(cache=None, question=None, partition=None):
                same = self._verifier.same_answer(question, candidate)
        except VerifierError:
            raise
        except Exception as exc:
            raise VerifierError(
                f"verifier {self._verifier.model_id}: {type(exc).__name__}"
            ) from exc
        if type(same) is not bool:  # a truthy "no" must not become a hit
            raise VerifierError(
                f"verifier {self._verifier.model_id} returned {type(same).__name__}, not bool"
            )
        if not same:
            with self._lock:
                self.verifier_rejects += 1
            log.debug("lite-cache: the verifier refused L2 candidate %s", key[:12])
        return same

    def _remember(self, call: Call, vector: list[float]) -> None:
        key = id(call)

        def forget(ref: "weakref.ref[Call]") -> None:
            # the call is gone (it failed, or after() already took the vector): drop its entry,
            # unless the id now names a newer call's entry
            with self._lock:
                if (kept := self._vectors.get(key)) is not None and kept[0] is ref:
                    self._vectors.pop(key, None)

        with self._lock:
            self._vectors[key] = (weakref.ref(call, forget), vector)
            while len(self._vectors) > 1024:
                self._vectors.popitem(last=False)

    def _take_vector(self, call: Call) -> tuple["weakref.ref[Call]", list[float]] | None:
        with self._lock:
            return self._vectors.pop(id(call), None)

    def _vector_for(self, call: Call, embedder: Embedder, question: str) -> list[float]:
        kept = self._take_vector(call)
        if kept is not None and kept[0]() is call:
            return kept[1]
        return _embed(embedder, question)  # no lookup ran (refresh=True): embed for the write

    def _count_hit(self, call: Call, scope: str, outcome: str, entry: Entry, now: datetime) -> None:
        self._safely(
            f"count a {outcome}",
            lambda: self._store.count(now.date(), scope, call.role, outcome, saved=entry.usage),
        )

    def after(self, call: Call, reply: Reply) -> None:
        cached = _cached(call)
        if cached is None:
            return
        policy, scope = cached
        now = self._clock()
        # A reply the endpoint served from its own cache (the LiteLLM Proxy's, lite-model 0.3.0)
        # cost nothing this time: store it, but count nothing as spent.
        spent = Usage() if reply.cached else reply.usage

        def store() -> None:
            key = cache_key(call, scope)  # inside: a request that cannot be keyed loses no reply
            reason = policy.accept(reply) if policy.accept is not None else None
            if reason is not None:
                outcome = f"skip:{reason}"
                self._store.count(now.date(), scope, call.role, outcome, spent=spent)
                return
            entry = _entry(call, reply, key, scope, policy, now)
            wanted = _semantic(call, policy)
            if wanted is not None and self._embedder is not None:
                question, partition = wanted
                entry = dataclasses.replace(
                    entry,
                    partition=partition,
                    embedder=self._embedder.model_id,
                    question_embedding=self._vector_for(call, self._embedder, question),
                )
            self._store.put_and_count(entry, now, now.date(), spent=spent)

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
            with self._lock:
                self.store_errors += 1
            log.warning("lite-cache could not %s: %s: %s", what, type(exc).__name__, exc)


def _embed(embedder: Embedder, question: str) -> list[float]:
    """The question's vector; any failure is a read failure (EmbedderError): nothing is sent."""
    try:
        vector = embedder.embed_query(question)
    except Exception as exc:
        raise EmbedderError(f"embedder {embedder.model_id}: {type(exc).__name__}: {exc}") from exc
    if len(vector) != embedder.dim:
        raise EmbedderError(
            f"embedder {embedder.model_id} returned {len(vector)} values, not {embedder.dim}"
        )
    if not all(math.isfinite(x) for x in vector):
        raise EmbedderError(f"embedder {embedder.model_id} returned values that are not finite")
    if not any(vector):
        # a zero vector has no direction: its cosine with anything is NaN (design §3.5)
        raise EmbedderError(f"embedder {embedder.model_id} returned a zero vector")
    return vector


def _semantic(call: Call, policy: Policy) -> tuple[str, str] | None:
    """(question, partition) when the call asks for L2 and gives both; None means L1 only."""
    question, partition = call.context.get("question"), call.context.get("partition")
    if not policy.semantic or not isinstance(question, str) or not isinstance(partition, str):
        return None
    return question, partition


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


class AsyncCacheHook:
    """`CacheHook` for an `AsyncLiteModel` (lite-model `AsyncHook`; design §9, llm-steward). The
    rules, the store and every counter are `CacheHook`'s: each `before` / `after` runs the sync
    hook in a worker thread (`asyncio.to_thread`), so a PostgreSQL round trip never blocks the
    event loop. `to_thread` copies the caller's contextvars, so `call_context` (and the verifier's
    own, design §13) holds in that thread. A native async store is not needed for an L1 lookup
    (one indexed SELECT); it would duplicate `PgStore` (design §9). The counters and admin calls
    below only forward to `sync`: a counter added to `CacheHook` is added here too."""

    name = CacheHook.name

    def __init__(
        self,
        store: Store,
        clock: Callable[[], datetime] = _utcnow,
        *,
        embedder: Embedder | None = None,
        verifier: Verifier | None = None,
        thresholds: Mapping[str, float] | None = None,
    ) -> None:
        self.sync = CacheHook(
            store, clock, embedder=embedder, verifier=verifier, thresholds=thresholds
        )

    async def before(self, call: Call) -> Reply | None:
        if _cached(call) is None:  # nothing to read: no thread hop for an uncached call
            return None
        return await asyncio.to_thread(self.sync.before, call)

    async def after(self, call: Call, reply: Reply) -> None:
        if _cached(call) is not None:
            await asyncio.to_thread(self.sync.after, call, reply)

    @property
    def store_errors(self) -> int:
        return self.sync.store_errors

    @property
    def verifier_rejects(self) -> int:
        return self.sync.verifier_rejects

    def invalidate(
        self, *, scope: str | None = None, role: str | None = None, key: str | None = None
    ) -> int:
        """`CacheHook.invalidate`; a sync call (an admin action, not a request path)."""
        return self.sync.invalidate(scope=scope, role=role, key=key)

    def stats(self, *, scope: str | None = None, since: date | None = None) -> list[RoleStats]:
        """`CacheHook.stats`; a sync call."""
        return self.sync.stats(scope=scope, since=since)
