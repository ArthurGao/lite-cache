"""Shared fixtures. PostgreSQL tests use `LITECACHE_TEST_DATABASE_URL` — set explicitly, never
inherited from the app's URL — and only ever the `lite_cache_test` schema, which they drop and
recreate (AGENTS.md rule 7)."""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

TEST_SCHEMA = "lite_cache_test"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Settings must not pick up the developer's environment or `.env` in tests."""
    for name in list(os.environ):
        if name.startswith("LITECACHE_") and name != "LITECACHE_TEST_DATABASE_URL":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def pg_url() -> str:
    url = os.environ.get("LITECACHE_TEST_DATABASE_URL")
    if not url:
        pytest.fail(
            "LITECACHE_TEST_DATABASE_URL is not set: PostgreSQL tests need it (see .env.example)"
        )
    return url


@pytest.fixture
def pg(pg_url: str) -> Iterator[psycopg.Connection]:
    """A connection with an empty `lite_cache_test` schema (dropped again afterwards)."""
    with psycopg.connect(pg_url, autocommit=True) as conn:
        drop = sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(TEST_SCHEMA))
        conn.execute(drop)
        yield conn
        conn.execute(drop)
