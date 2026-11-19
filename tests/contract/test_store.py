"""`PgStore` entries (design §3.3, §4): round trip, TTL, overwrite, invalidation, purge."""

import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from lite_model import Usage
from psycopg import sql

from lite_cache.errors import StoreError
from lite_cache.store import PgStore
from lite_cache.types import Entry
from tests.conftest import TEST_SCHEMA

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
PROVENANCE = {
    "role": "answer",
    "backend": "ollama",
    "requested": "qwen3:8b",
    "answered": "qwen3:8b",
    "channel": "local",
    "routed": False,
    "via": {},
}


def entry(key: str = "k1", scope: str = "acme", **changes: object) -> Entry:
    base = Entry(
        key=key,
        scope=scope,
        role="answer",
        backend="ollama",
        model="qwen3:8b",
        question=None,
        kind="text",
        payload={"text": "hello"},
        provenance=PROVENANCE,
        usage=Usage(),
        created_at=NOW,
        expires_at=None,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def rows(pg: psycopg.Connection) -> list[tuple[str, str]]:
    query = sql.SQL("SELECT key, scope FROM {}.entries ORDER BY key").format(
        sql.Identifier(TEST_SCHEMA)
    )
    return [(k, s) for k, s in pg.execute(query)]


def test_put_then_get_round_trips_every_field(store: PgStore) -> None:
    bare = entry("k1")
    full = entry(
        "k2",
        scope="acme/u42",
        role="graph",
        question="What is the brew ratio?",
        kind="structured",
        payload={"name": "Auckland", "n": [1, 2.5]},
        provenance={**PROVENANCE, "answered": None, "via": {"x-litellm-model-id": "m1"}},
        usage=Usage(input=10, output=5, reasoning=2, cost_usd=0.01),
        expires_at=NOW + timedelta(hours=1),
    )
    store.put(bare, NOW)
    store.put(full, NOW)
    assert store.get("k1", NOW) == bare
    assert store.get("k2", NOW) == full
    assert store.get("missing", NOW) is None


def test_an_expired_entry_is_not_returned(store: PgStore) -> None:
    store.put(entry(expires_at=NOW + timedelta(seconds=10)), NOW)
    assert store.get("k1", NOW + timedelta(seconds=9)) is not None
    assert store.get("k1", NOW + timedelta(seconds=10)) is None
    assert store.get("k1", NOW + timedelta(seconds=11)) is None


def test_put_overwrites_the_same_key(store: PgStore, pg: psycopg.Connection) -> None:
    # Two misses of one request racing (no single-flight): both write, one row stays, no error.
    errors: list[BaseException] = []

    def write(text: str) -> None:
        try:
            store.put(entry(payload={"text": text}), NOW)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(t,)) for t in ("first", "second")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert rows(pg) == [("k1", "acme")]
    got = store.get("k1", NOW)
    assert got is not None and got.payload in ({"text": "first"}, {"text": "second"})


def test_scope_prefix_invalidation(store: PgStore, pg: psycopg.Connection) -> None:
    scopes = ["a", "a/x.y", "a/b/c", "ab", "a_b", "a.b"]
    for i, scope in enumerate(scopes):
        store.put(entry(f"k{i}", scope=scope), NOW)
    assert store.invalidate(scope="a") == 3
    assert sorted(s for _, s in rows(pg)) == ["a.b", "a_b", "ab"]


def test_invalidate_by_role_key_and_their_intersection(store: PgStore) -> None:
    store.put(entry("k1", scope="acme", role="answer"), NOW)
    store.put(entry("k2", scope="acme", role="graph"), NOW)
    store.put(entry("k3", scope="other", role="graph"), NOW)
    store.put(entry("k4", scope="other", role="answer"), NOW)
    assert store.invalidate(scope="acme", role="graph") == 1  # k2
    assert store.invalidate(key="k1") == 1
    assert store.invalidate(role="graph") == 1  # k3
    assert store.get("k4", NOW) is not None
    with pytest.raises(ValueError):
        store.invalidate()


def test_purge_removes_only_expired_and_respects_the_limit(store: PgStore) -> None:
    for i in range(3):
        store.put(entry(f"old{i}", expires_at=NOW + timedelta(seconds=1)), NOW)
    store.put(entry("live", expires_at=NOW + timedelta(days=1)), NOW)
    store.put(entry("forever"), NOW)
    later = NOW + timedelta(minutes=1)
    assert store.purge_expired(later, limit=2) == 2
    assert store.purge_expired(later) == 1
    assert store.get("live", later) is not None
    assert store.get("forever", later) is not None


def test_put_purges_at_most_once_an_hour(store: PgStore, pg: psycopg.Connection) -> None:
    store.put(entry("short", expires_at=NOW + timedelta(seconds=10)), NOW)  # first put purges
    store.put(entry("b"), NOW + timedelta(seconds=20))  # within the hour: no purge
    assert ("short", "acme") in rows(pg)
    store.put(entry("c"), NOW + timedelta(hours=1, seconds=1))  # an hour later: purges
    assert ("short", "acme") not in rows(pg)


def test_a_database_error_is_a_store_error(store: PgStore, pg: psycopg.Connection) -> None:
    pg.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(TEST_SCHEMA)))
    with pytest.raises(StoreError):
        store.get("k1", NOW)
