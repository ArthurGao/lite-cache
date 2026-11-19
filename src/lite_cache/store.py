"""The store (design §4): `PgStore` over a psycopg pool; `open_store` migrates when behind."""

from psycopg_pool import ConnectionPool

from lite_cache import db
from lite_cache.config import Settings


class PgStore:
    """PostgreSQL store in one schema; every operation is one transaction (filled in S1)."""

    def __init__(self, pool: ConnectionPool, schema: str) -> None:
        self._pool = pool
        self._schema = schema

    def close(self) -> None:
        self._pool.close()


def open_store(settings: Settings | None = None) -> PgStore:
    """The store for `settings` (default: from the environment). Checks the applied migrations
    without a lock and migrates only when behind (§4); open it once per process."""
    settings = settings or Settings()
    url = db.require_url(settings.database_url)
    with db.connect(url) as conn:
        if db.pending(conn, settings.schema_name):
            db.migrate(conn, settings.schema_name)
    pool = ConnectionPool(url, min_size=1, open=True)
    return PgStore(pool, settings.schema_name)
