"""Counting (design §3.4): one upsert per call, outcomes apart, tokens saved and spent."""

import threading
from datetime import date, timedelta

import psycopg
import pytest
from lite_model import Usage
from psycopg import sql

from lite_cache.errors import StoreError
from lite_cache.store import PgStore
from lite_cache.types import RoleStats
from tests.conftest import TEST_SCHEMA
from tests.contract.test_store import NOW, entry

DAY = NOW.date()
SPENT = Usage(input=100, output=20)


def test_counts_add_up_per_outcome(store: PgStore) -> None:
    store.count(DAY, "acme", "answer", "hit", saved=SPENT)
    store.count(DAY, "acme", "answer", "hit", saved=SPENT)
    store.put_and_count(entry(), NOW, DAY, spent=SPENT)
    store.count(DAY, "acme", "answer", "skip:empty", spent=Usage(input=7, output=3))
    [stats] = store.stats()
    assert stats == RoleStats(
        role="answer",
        hits=2,
        stored=1,
        skips={"empty": 1},
        saved_input_tokens=200,
        saved_output_tokens=40,
        spent_input_tokens=107,
        spent_output_tokens=23,
    )
    assert stats.hit_rate == 0.5


def test_unknown_usage_counts_as_zero(store: PgStore) -> None:
    store.count(DAY, "acme", "answer", "skip:x", spent=Usage())
    store.count(DAY, "acme", "answer", "hit", saved=Usage())
    [stats] = store.stats()
    assert (stats.hits, stats.skips) == (1, {"x": 1})
    assert stats.saved_input_tokens == stats.spent_input_tokens == 0


def test_no_calls_have_no_hit_rate() -> None:
    assert RoleStats("answer", 0, 0, {}, 0, 0, 0, 0).hit_rate is None


def test_concurrent_upserts_lose_nothing(store: PgStore) -> None:
    def hits() -> None:
        for _ in range(25):
            store.count(DAY, "acme", "answer", "hit", saved=Usage(input=1))

    threads = [threading.Thread(target=hits) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    [stats] = store.stats()
    assert (stats.hits, stats.saved_input_tokens) == (200, 200)


def test_stats_filters_by_scope_prefix_and_since(store: PgStore) -> None:
    yesterday = DAY - timedelta(days=1)
    store.count(DAY, "a/u1", "answer", "hit")
    store.count(yesterday, "a/u1", "answer", "hit")
    store.count(DAY, "ab", "answer", "hit")
    store.count(DAY, "a/u2", "graph", "hit")
    by_role = {s.role: s.hits for s in store.stats(scope="a", since=DAY)}
    assert by_role == {"answer": 1, "graph": 1}
    assert {s.role: s.hits for s in store.stats()} == {"answer": 3, "graph": 1}


def test_put_and_count_is_atomic(store: PgStore, pg: psycopg.Connection) -> None:
    _raise_on(pg, "stats", "INSERT")  # the count fails inside the database
    with pytest.raises(StoreError):
        store.put_and_count(entry(), NOW, DAY, spent=SPENT)
    assert store.get("k1", NOW) is None  # rolled back with the count


def test_since_takes_a_date(store: PgStore) -> None:
    store.count(date(2026, 1, 1), "acme", "answer", "hit")
    assert store.stats(since=date(2026, 1, 2)) == []


def _raise_on(pg: psycopg.Connection, table: str, event: str) -> None:
    """Make every `event` on `table` in the test schema fail inside the database."""
    pg.execute(
        sql.SQL(
            "CREATE FUNCTION {schema}.refuse() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'refused by test'; END $$; "
            "CREATE TRIGGER refuse BEFORE {event} ON {schema}.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION {schema}.refuse()"
        ).format(
            schema=sql.Identifier(TEST_SCHEMA), table=sql.Identifier(table), event=sql.SQL(event)
        )
    )


def test_a_failed_purge_does_not_fail_the_write(
    store: PgStore, pg: psycopg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    _raise_on(pg, "entries", "DELETE")  # the opportunistic purge after the write fails
    store.put_and_count(entry(), NOW, DAY, spent=SPENT)
    assert store.get("k1", NOW) is not None
    assert [s.stored for s in store.stats()] == [1]
    assert "purge" in caplog.text
