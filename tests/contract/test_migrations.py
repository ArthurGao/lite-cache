"""Migrations (design §4): the runner vendored from lite-rag, and `open_store` migrating on open."""

import psycopg

from lite_cache.config import Settings
from lite_cache.db import migrate, pending
from lite_cache.store import open_store
from tests.conftest import TEST_SCHEMA


def test_migrate_applies_once(pg: psycopg.Connection) -> None:
    every = ["0001_initial", "0002_semantic"]  # S3 added the second
    assert pending(pg, TEST_SCHEMA) == every
    assert migrate(pg, TEST_SCHEMA) == every
    assert migrate(pg, TEST_SCHEMA) == []
    assert pending(pg, TEST_SCHEMA) == []


def test_the_tables_exist_and_scope_is_c_collated(pg: psycopg.Connection) -> None:
    migrate(pg, TEST_SCHEMA)
    tables = {
        row[0]
        for row in pg.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (TEST_SCHEMA,),
        )
    }
    assert {"entries", "stats", "schema_migrations"} <= tables
    collations = pg.execute(
        "SELECT table_name, collation_name FROM information_schema.columns "
        "WHERE table_schema = %s AND column_name = 'scope'",
        (TEST_SCHEMA,),
    ).fetchall()
    assert sorted(collations) == [("entries", "C"), ("stats", "C")]


def test_open_store_migrates_a_fresh_schema(pg: psycopg.Connection, pg_url: str) -> None:
    store = open_store(Settings(database_url=pg_url, schema_name=TEST_SCHEMA))
    try:
        assert pending(pg, TEST_SCHEMA) == []
    finally:
        store.close()
