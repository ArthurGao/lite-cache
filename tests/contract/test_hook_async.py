"""`AsyncCacheHook` on a real `AsyncLiteModel` (design §9: llm-steward is async). The store stays
the sync `PgStore`; its calls run in a worker thread so the event loop is never blocked. The
rules are `CacheHook`'s; these tests check that they hold through the async path."""

import asyncio
import contextvars
import threading
from datetime import datetime
from typing import Any

import pytest
from lite_model import AsyncLiteModel, StructuredReply, TextReply, call_context
from pydantic import BaseModel

from lite_cache import AsyncCacheHook, CacheHook, Policy, StoreError
from lite_cache.errors import ConfigError
from lite_cache.store import PgStore
from lite_cache.types import Entry
from tests.contract.test_hook import USAGE, Clock, FailingRead, FailingWrites
from tests.contract.test_hook_semantic import FakeEmbedder
from tests.helpers import ASK, async_local_model, local_model
from tests.upstream import Upstream, completion


class Risk(BaseModel):
    level: str


def _make(store: PgStore, *replies: Any) -> tuple[AsyncLiteModel, Upstream, AsyncCacheHook]:
    up = Upstream(*(replies or [(200, completion("fresh", usage=USAGE))]))
    model = async_local_model(up)
    hook = AsyncCacheHook(store, clock=Clock())
    model.hooks.register(hook)
    return model, up, hook


def test_the_second_identical_call_sends_nothing(store: PgStore) -> None:
    model, up, hook = _make(store)

    async def twice() -> tuple[TextReply, TextReply]:
        with call_context(scope="steward/ns-a", cache=Policy()):
            return await model.text("answer", ASK), await model.text("answer", ASK)

    first, second = asyncio.run(twice())
    assert len(up.requests) == 1
    assert (first.text, first.cached) == ("fresh", False)
    assert (second.text, second.cached) == ("fresh", True)
    assert second.provenance == first.provenance
    [stats] = hook.stats(scope="steward/ns-a")
    assert (stats.role, stats.hits, stats.stored) == ("answer", 1, 1)


def test_scopes_never_share_an_entry(store: PgStore) -> None:
    model, up, _ = _make(store)

    async def run() -> None:
        for scope in ("steward/ns-a", "steward/ns-b", "steward/ns-a"):
            with call_context(scope=scope, cache=Policy()):
                await model.text("answer", ASK)

    asyncio.run(run())
    assert len(up.requests) == 2  # ns-a, ns-b; then ns-a from the cache


def test_structured_replies_are_cached_with_their_schema(store: PgStore) -> None:
    model, up, _ = _make(store, (200, completion('{"level": "high"}', usage=USAGE)))

    async def twice() -> list[StructuredReply[Risk]]:
        with call_context(scope="steward/ns-a", cache=Policy()):
            return [await model.structured("answer", ASK, Risk) for _ in range(2)]

    first, second = asyncio.run(twice())
    assert len(up.requests) == 1
    assert not first.cached
    assert second.value == Risk(level="high") and second.cached


class ThreadSpy(PgStore):
    """Records the thread every lookup runs on."""

    seen: list[str]

    def get(self, key: str, now: datetime) -> Entry | None:
        self.seen.append(threading.current_thread().name)
        return super().get(key, now)


def test_without_a_policy_nothing_is_cached_and_no_thread_is_used(store: PgStore) -> None:
    spy = ThreadSpy(store._pool, store._schema)
    spy.seen = []
    model, up, hook = _make(spy)

    async def twice() -> None:
        with call_context(scope="steward/ns-a"):
            await model.text("answer", ASK)
            await model.text("answer", ASK)

    asyncio.run(twice())
    assert len(up.requests) == 2
    assert (hook.stats(), spy.seen) == ([], [])


def test_store_calls_run_off_the_event_loop(store: PgStore) -> None:
    spy = ThreadSpy(store._pool, store._schema)
    spy.seen = []
    model, _, _ = _make(spy)

    async def once() -> str:
        with call_context(scope="steward/ns-a", cache=Policy()):
            await model.text("answer", ASK)
        return threading.current_thread().name

    loop_thread = asyncio.run(once())
    assert spy.seen and all(name != loop_thread for name in spy.seen)


def test_a_read_failure_fails_the_call_unsent(store: PgStore) -> None:
    model, up, _ = _make(FailingRead(store._pool, store._schema))

    async def once() -> None:
        with call_context(scope="steward/ns-a", cache=Policy()):
            await model.text("answer", ASK)

    with pytest.raises(StoreError):
        asyncio.run(once())
    assert up.requests == []


def test_a_write_failure_returns_the_reply_and_is_counted(store: PgStore) -> None:
    model, up, hook = _make(FailingWrites(store._pool, store._schema))

    async def once() -> TextReply:
        with call_context(scope="steward/ns-a", cache=Policy()):
            return await model.text("answer", ASK)

    reply = asyncio.run(once())
    assert (reply.text, reply.cached, len(up.requests)) == ("fresh", False, 1)
    assert hook.store_errors == 1


REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class ContextSpy(PgStore):
    seen: list[str]

    def get(self, key: str, now: datetime) -> Entry | None:
        self.seen.append(REQUEST_ID.get())
        return None


def test_the_worker_thread_sees_the_callers_context(store: PgStore) -> None:
    # asyncio.to_thread copies contextvars, so the verifier's own call_context (and a host's
    # request-scoped vars) hold in the thread that runs the sync hook
    spy = ContextSpy(store._pool, store._schema)
    spy.seen = []
    model, _, _ = _make(spy)

    async def once() -> None:
        REQUEST_ID.set("req-7")
        with call_context(scope="steward/ns-z", cache=Policy()):
            await model.text("answer", ASK)

    asyncio.run(once())
    assert spy.seen == ["req-7"]


def test_a_sync_model_refuses_the_async_hook(store: PgStore) -> None:
    with pytest.raises(TypeError):
        local_model(Upstream()).hooks.register(AsyncCacheHook(store))


def test_it_shares_cache_hooks_construction_checks(store: PgStore) -> None:
    with pytest.raises(ConfigError, match="no calibrated L2 threshold"):
        AsyncCacheHook(store, embedder=FakeEmbedder(), thresholds={})
    assert AsyncCacheHook.name == CacheHook.name == "lite-cache"
