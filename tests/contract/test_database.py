"""The test database itself: what the other PostgreSQL tests rely on."""

import psycopg

from lite_cache.db import check_vector, migrate
from tests.conftest import TEST_SCHEMA


def test_the_test_database_has_a_non_c_default_collation(pg: psycopg.Connection) -> None:
    # Design §3.3: scope prefix invalidation is proven only if the column's C collation, not the
    # database default, does the work.
    row = pg.execute(
        "SELECT datcollate FROM pg_database WHERE datname = current_database()"
    ).fetchone()
    assert row is not None
    assert row[0] not in ("C", "POSIX")


def test_pgvector_is_usable_and_new_enough(pg: psycopg.Connection) -> None:
    # migrate() creates the extension; check_vector() runs a real vector operation and wants
    # pgvector >= 0.8 (presence alone proves nothing: KB rag-production-guide §2).
    migrate(pg, TEST_SCHEMA)
    check_vector(pg)
