"""`CacheHook` on a real `LiteModel` (design §2, §3, §3.2, §3.5; S1 acceptance in §11).
Requests go to a recording transport, so a hit is "no request was sent"."""

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

import psycopg
import pytest
from lite_model import (
    EmptyReply,
    LiteModel,
    Provenance,
    StructuredReply,
    TextReply,
    Truncated,
    Usage,
    call_context,
)
from psycopg import sql
from pydantic import BaseModel, Field, field_validator

from lite_cache import CacheHook, InvalidScope, Policy, StoreError
from lite_cache.keys import cache_key
from lite_cache.store import PgStore
from lite_cache.types import Entry, RoleStats
from tests.conftest import TEST_SCHEMA
from tests.helpers import ASK, a_call, local_model
from tests.upstream import Upstream, completion

T0 = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}


class City(BaseModel):
    name: str


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


Make = Callable[..., tuple[LiteModel, Upstream, CacheHook]]


@pytest.fixture
def make(store: PgStore, clock: Clock) -> Make:
    def build(*replies: Any, on: PgStore | None = None) -> tuple[LiteModel, Upstream, CacheHook]:
        up = Upstream(*(replies or [(200, completion("fresh", usage=USAGE))]))
        model = local_model(up)
        hook = CacheHook(on or store, clock=clock)
        model.hooks.register(hook)
        return model, up, hook

    return build


def count_entries(pg: psycopg.Connection) -> int:
    query = sql.SQL("SELECT count(*) FROM {}.entries").format(sql.Identifier(TEST_SCHEMA))
    row = pg.execute(query).fetchone()
    assert row is not None
    return int(row[0])


def test_the_second_identical_call_sends_nothing(make: Make) -> None:
    model, up, hook = make()
    with call_context(scope="acme", cache=True):
        first = model.text("answer", ASK)
        second = model.text("answer", ASK)
    assert len(up.requests) == 1
    assert (first.cached, second.cached) == (False, True)
    assert (second.text, second.provenance, second.usage) == (
        first.text,
        first.provenance,
        first.usage,
    )
    [stats] = hook.stats()
    assert (stats.hits, stats.stored) == (1, 1)
    assert (stats.saved_input_tokens, stats.spent_input_tokens) == (100, 100)


@pytest.mark.parametrize(
    "context",
    [{}, {"scope": "acme"}, {"cache": True}, {"scope": "acme", "cache": False}],
    ids=["none", "scope-only", "policy-only", "cache-false"],
)
def test_without_scope_or_policy_nothing_is_read_or_written(
    make: Make, pg: psycopg.Connection, context: dict[str, Any]
) -> None:
    model, up, hook = make()
    with call_context(**context):
        model.text("answer", ASK)
        model.text("answer", ASK)
    assert len(up.requests) == 2
    assert count_entries(pg) == 0
    assert hook.stats() == []


def test_question_is_stored_but_does_not_change_the_key(
    make: Make, store: PgStore, pg: psycopg.Connection
) -> None:
    model, up, _ = make()
    with call_context(scope="acme", cache=True, question="first wording"):
        model.text("answer", ASK)
    with call_context(scope="acme", cache=True, question="second wording"):
        assert model.text("answer", ASK).cached
    assert len(up.requests) == 1
    query = sql.SQL("SELECT question FROM {}.entries").format(sql.Identifier(TEST_SCHEMA))
    assert pg.execute(query).fetchall() == [("first wording",)]


def test_structured_hit_restores_the_schema(make: Make) -> None:
    model, up, _ = make((200, completion('{"name": "Auckland"}')))
    with call_context(scope="acme", cache=True):
        model.structured("answer", ASK, City)
        reply = model.structured("answer", ASK, City)
    assert len(up.requests) == 1
    assert isinstance(reply, StructuredReply)
    assert reply.cached and reply.value == City(name="Auckland")


def test_a_structured_entry_that_no_longer_validates_is_a_miss(
    make: Make, pg: psycopg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    class Stricter(BaseModel):  # same JSON schema and name as City: the same key
        name: str

        @field_validator("name")
        @classmethod
        def not_auckland(cls, value: str) -> str:
            if value == "Auckland":
                raise ValueError("no longer allowed")
            return value

    Stricter.__name__ = "City"
    model, up, _ = make(
        (200, completion('{"name": "Auckland"}')), (200, completion('{"name": "Wellington"}'))
    )
    with call_context(scope="acme", cache=True):
        model.structured("answer", ASK, City)
        reply = model.structured("answer", ASK, Stricter)
    assert len(up.requests) == 2
    assert reply.cached is False and reply.value.name == "Wellington"
    assert count_entries(pg) == 1  # overwritten
    assert "no longer validates" in caplog.text


def test_accept_refusal_is_counted_and_not_stored(make: Make, pg: psycopg.Connection) -> None:
    model, _, hook = make()
    with call_context(scope="acme", cache=Policy(accept=lambda reply: "too_short")):
        model.text("answer", ASK)
    assert count_entries(pg) == 0
    [stats] = hook.stats()
    assert (stats.stored, stats.skips, stats.spent_input_tokens) == (0, {"too_short": 1}, 100)


def test_accept_that_raises_still_returns_the_reply(make: Make, pg: psycopg.Connection) -> None:
    def broken(reply: object) -> str | None:
        raise RuntimeError("caller's predicate is broken")

    model, _, hook = make()
    with call_context(scope="acme", cache=Policy(accept=broken)):
        reply = model.text("answer", ASK)
    assert reply.text == "fresh"
    assert count_entries(pg) == 0
    assert hook.store_errors == 1
    assert hook.stats() == []  # nothing counted


@pytest.mark.parametrize(
    ("body", "error"),
    [(completion("cut", finish="length"), Truncated), (completion(""), EmptyReply)],
    ids=["truncated", "empty"],
)
def test_truncated_and_empty_replies_are_neither_stored_nor_counted(
    make: Make, pg: psycopg.Connection, body: dict[str, Any], error: type[Exception]
) -> None:
    model, _, hook = make((200, body))
    with call_context(scope="acme", cache=True), pytest.raises(error):
        model.text("answer", ASK)
    assert count_entries(pg) == 0
    assert hook.stats() == []


def test_an_expired_entry_is_called_again(make: Make, clock: Clock) -> None:
    model, up, _ = make()
    with call_context(scope="acme", cache=Policy(ttl_s=60)):
        model.text("answer", ASK)
        clock.now = T0 + timedelta(seconds=59)
        assert model.text("answer", ASK).cached
        clock.now = T0 + timedelta(seconds=61)
        assert not model.text("answer", ASK).cached
    assert len(up.requests) == 2


def test_refresh_skips_the_read_and_overwrites(make: Make) -> None:
    model, up, hook = make()
    with call_context(scope="acme", cache=True):
        model.text("answer", ASK)
    with call_context(scope="acme", cache=Policy(refresh=True)):
        assert not model.text("answer", ASK).cached
    assert len(up.requests) == 2
    [stats] = hook.stats()
    assert (stats.hits, stats.stored) == (0, 2)


def test_unknown_usage_and_provenance_round_trip(make: Make, store: PgStore) -> None:
    model, up, hook = make()
    provenance = Provenance(
        "answer",
        "ollama",
        "qwen3:8b",
        None,
        "local",
        routed=False,
        via={"x-litellm-model-id": "m1"},
    )
    call = a_call()
    store.put(
        Entry(
            key=cache_key(call, "acme"),
            scope="acme",
            role="answer",
            backend="ollama",
            model="qwen3:8b",
            question=None,
            kind="text",
            payload={"text": "stored"},
            provenance={
                "role": "answer",
                "backend": "ollama",
                "requested": "qwen3:8b",
                "answered": None,
                "channel": "local",
                "routed": False,
                "via": {"x-litellm-model-id": "m1"},
            },
            usage=Usage(),
            created_at=T0,
            expires_at=None,
        ),
        T0,
    )
    with call_context(scope="acme", cache=True):
        reply = model.text("answer", ASK)
    assert up.requests == []
    assert reply.provenance == provenance
    assert reply.provenance.tag.endswith("?")
    assert reply.usage == Usage()
    [stats] = hook.stats()
    assert (stats.hits, stats.saved_input_tokens) == (1, 0)


class FailingRead(PgStore):
    def get(self, key: str, now: datetime) -> Entry | None:
        raise StoreError("database down")


class FailingWrites(PgStore):
    def put_and_count(self, entry: Entry, now: datetime, day: Any, *, spent: Usage) -> None:
        raise StoreError("write failed")

    def count(self, *args: Any, **kwargs: Any) -> None:
        raise StoreError("count failed")


def test_a_read_failure_raises_and_sends_nothing(make: Make, store: PgStore) -> None:
    model, up, _ = make(on=FailingRead(store._pool, store._schema))
    with call_context(scope="acme", cache=True), pytest.raises(StoreError):
        model.text("answer", ASK)
    assert up.requests == []


def test_write_and_count_failures_never_fail_the_call(make: Make, store: PgStore) -> None:
    failing = FailingWrites(store._pool, store._schema)
    model, _, hook = make(on=failing)
    with call_context(scope="acme", cache=True):
        assert model.text("answer", ASK).text == "fresh"  # put_and_count fails
    with call_context(scope="acme", cache=Policy(accept=lambda r: "no")):
        assert model.text("answer", ASK).text == "fresh"  # count fails
    assert hook.store_errors == 2


def test_a_hit_whose_count_fails_is_still_returned(make: Make, store: PgStore) -> None:
    writer, _, _ = make()
    with call_context(scope="acme", cache=True):
        writer.text("answer", ASK)
    reader, up, hook = make(on=FailingWrites(store._pool, store._schema))
    with call_context(scope="acme", cache=True):
        assert reader.text("answer", ASK).cached
    assert up.requests == []
    assert hook.store_errors == 1


def test_invalid_scope_fails_before_sending(make: Make) -> None:
    model, up, _ = make()
    with call_context(scope="a b", cache=True), pytest.raises(InvalidScope):
        model.text("answer", ASK)
    assert up.requests == []


def test_a_bad_cache_value_fails_before_sending(make: Make) -> None:
    model, up, _ = make()
    with call_context(scope="acme", cache="yes"), pytest.raises(TypeError):
        model.text("answer", ASK)
    assert up.requests == []


def test_two_scopes_never_share(make: Make) -> None:
    model, up, _ = make()
    with call_context(scope="acme/u1", cache=True):
        model.text("answer", ASK)
    with call_context(scope="acme/u2", cache=True):
        assert not model.text("answer", ASK).cached
    assert len(up.requests) == 2


def test_invalidate_through_the_hook(make: Make) -> None:
    model, up, hook = make()
    with call_context(scope="acme/u1", cache=True):
        model.text("answer", ASK)
        assert hook.invalidate(scope="acme") == 1
        assert not model.text("answer", ASK).cached
    assert len(up.requests) == 2
    assert isinstance(hook.stats()[0], RoleStats)


# --- final review fixes ---


class Aliased(BaseModel):
    from_: str = Field(alias="from")


def test_a_schema_with_aliases_hits(make: Make) -> None:
    model, up, _ = make((200, completion('{"from": "Auckland"}')))
    with call_context(scope="acme", cache=True):
        model.structured("answer", ASK, Aliased)
        reply = model.structured("answer", ASK, Aliased)
    assert len(up.requests) == 1
    assert reply.cached and reply.value.from_ == "Auckland"


def test_a_stored_row_of_an_older_shape_is_a_miss_not_a_failure(
    make: Make, store: PgStore, caplog: pytest.LogCaptureFixture
) -> None:
    model, up, _ = make()
    call = a_call()
    old = Entry(
        key=cache_key(call, "acme"),
        scope="acme",
        role="answer",
        backend="ollama",
        model="qwen3:8b",
        question=None,
        kind="text",
        payload={"content": "an older payload shape"},
        provenance={"role": "answer", "renamed_field": "x"},
        usage=Usage(),
        created_at=T0,
        expires_at=None,
    )
    store.put(old, T0)
    with call_context(scope="acme", cache=True):
        reply = model.text("answer", ASK)
    assert reply.text == "fresh" and not reply.cached
    assert len(up.requests) == 1
    assert "could not restore" in caplog.text


def test_a_key_error_after_a_paid_call_keeps_the_reply(store: PgStore) -> None:
    hook = CacheHook(store, clock=Clock())
    call = a_call(
        params={"temperature": math.nan},
        context={"scope": "acme", "cache": Policy(refresh=True)},
    )
    reply = TextReply(
        "paid", Provenance("answer", "ollama", "qwen3:8b", "qwen3:8b", "local", False), Usage()
    )
    assert hook.before(call) is None  # refresh: no key built here
    hook.after(call, reply)  # NaN in params cannot be keyed: must not raise
    assert hook.store_errors == 1


def test_the_hooks_own_provenance_conversion_round_trips(store: PgStore) -> None:
    hook = CacheHook(store, clock=Clock())
    call = a_call(context={"scope": "acme", "cache": True})
    provenance = Provenance(
        "answer",
        "litellm",
        "smart",
        None,
        "api",
        routed=True,
        via=MappingProxyType({"x-litellm-model-id": "m1"}),
    )
    hook.after(call, TextReply("routed", provenance, Usage(input=3)))
    hit = hook.before(call)
    assert isinstance(hit, TextReply)
    assert hit.provenance == provenance and dict(hit.provenance.via) == {"x-litellm-model-id": "m1"}
    assert hit.usage == Usage(input=3)
