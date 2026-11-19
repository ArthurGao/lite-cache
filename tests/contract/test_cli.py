"""The `litecache` CLI (design §6) against the test schema."""

import psycopg
import pytest
from typer.testing import CliRunner

from lite_cache.cli import app
from lite_cache.store import PgStore
from tests.conftest import TEST_SCHEMA
from tests.contract.test_store import NOW, entry

runner = CliRunner()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, pg: psycopg.Connection, pg_url: str) -> str:
    monkeypatch.setenv("LITECACHE_DATABASE_URL", pg_url)
    monkeypatch.setenv("LITECACHE_SCHEMA", TEST_SCHEMA)
    return pg_url


def test_migrate_applies_then_is_up_to_date(env: str) -> None:
    first = runner.invoke(app, ["migrate"])
    assert first.exit_code == 0, first.output
    assert "0001_initial" in first.output
    second = runner.invoke(app, ["migrate"])
    assert second.exit_code == 0
    assert "up to date" in second.output


def test_invalidate_needs_a_condition(env: str) -> None:
    result = runner.invoke(app, ["invalidate"])
    assert result.exit_code == 2


def test_invalidate_prints_the_count(env: str, store: PgStore) -> None:
    store.put(entry("k1", scope="a/u1"), NOW)
    store.put(entry("k2", scope="ab"), NOW)
    result = runner.invoke(app, ["invalidate", "--scope", "a"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "1"


def test_purge_prints_the_count(env: str, store: PgStore) -> None:
    result = runner.invoke(app, ["purge"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "0"


def test_stats_prints_one_line_per_role(env: str, store: PgStore) -> None:
    store.count(NOW.date(), "acme", "answer", "hit")
    store.count(NOW.date(), "acme", "graph", "skip:empty")
    result = runner.invoke(app, ["stats", "--scope", "acme"])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert [line.split()[0] for line in lines] == ["answer", "graph"]
    assert "hits 1" in lines[0] and "hit_rate 1.00" in lines[0]
    assert "skip:empty 1" in lines[1]


def test_stats_rejects_a_bad_date(env: str) -> None:
    result = runner.invoke(app, ["stats", "--since", "not-a-date"])
    assert result.exit_code != 0
    assert "since" in result.output.lower()


def test_config_redacts_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    # Its own URL: the test must not depend on whether the test database has a password.
    monkeypatch.setenv("LITECACHE_DATABASE_URL", "postgresql://u:secret@h/db")
    monkeypatch.setenv("LITECACHE_SCHEMA", TEST_SCHEMA)
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0, result.output
    assert "***" in result.output
    assert "secret" not in result.output
    assert TEST_SCHEMA in result.output


def test_an_unreachable_database_is_a_message_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LITECACHE_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    result = runner.invoke(app, ["purge"])
    assert result.exit_code == 1
    assert "cannot connect to PostgreSQL" in result.stderr
    assert "Traceback" not in result.output


def test_stats_prints_semantic_hits(env: str, store: PgStore) -> None:
    store.count(NOW.date(), "acme", "answer", "hit")
    store.count(NOW.date(), "acme", "answer", "hit_semantic")
    store.count(NOW.date(), "acme", "answer", "hit_semantic")
    result = runner.invoke(app, ["stats", "--scope", "acme"])
    assert result.exit_code == 0, result.output
    [line] = result.output.strip().splitlines()
    assert "hits 1" in line and "semantic_hits 2" in line and "hit_rate 1.00" in line
