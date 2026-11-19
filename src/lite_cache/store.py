"""The store (design §4): `PgStore` over a psycopg pool; `open_store` migrates when behind."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Protocol

import psycopg
from lite_model import Usage
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from lite_cache import db
from lite_cache.config import Settings
from lite_cache.errors import StoreError
from lite_cache.types import Entry, RoleStats

log = logging.getLogger("lite_cache")

PURGE_EVERY = timedelta(hours=1)  # writes purge expired entries at most this often (§3.3)
PURGE_LIMIT = 1000  # ...and at most this many rows at a time
POOL_MAX = 8  # concurrent lookups from one process (threads, Bounded)
# Seconds a lookup waits for a pooled connection: a database gone after start fails visibly
# and soon (§3.5), not after the pool's default 30 s on every cached call.
POOL_TIMEOUT = 5.0

COLUMNS = (
    "key, scope, role, backend, model, question, kind, payload, provenance, input_tokens, "
    "output_tokens, reasoning_tokens, cost_usd, created_at, expires_at"
).split(", ")
# What a write sets: the read columns and the L2 ones (S3; NULL for an L1 write).
WRITTEN = [*COLUMNS, "partition", "embedder", "question_embedding"]
UPSERT = (
    f"INSERT INTO entries ({', '.join(WRITTEN)}) VALUES "
    f"({', '.join(['%s'] * (len(WRITTEN) - 1))}, %s::vector) "
    f"ON CONFLICT (key) DO UPDATE SET ({', '.join(WRITTEN[1:])}) = "
    f"({', '.join(f'EXCLUDED.{c}' for c in WRITTEN[1:])})"
)

TOKENS = ("saved_input_tokens", "saved_output_tokens", "spent_input_tokens", "spent_output_tokens")
# One counter row per (day, scope, role, outcome), added to in place (§3.4).
COUNT = (
    f"INSERT INTO stats AS s (day, scope, role, outcome, n, {', '.join(TOKENS)}) "
    "VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s) ON CONFLICT (day, scope, role, outcome) "
    f"DO UPDATE SET n = s.n + 1, {', '.join(f'{t} = s.{t} + EXCLUDED.{t}' for t in TOKENS)}"
)
SUM_TOKENS = ", ".join(f"sum({t}) AS {t}" for t in TOKENS)

UNEXPIRED = "(expires_at IS NULL OR expires_at > %(now)s)"  # needs the parameter `now`

# A scope and everything under it: `a` matches `a` and `a/...`, not `ab`. '0' follows '/' in byte
# order, and the C collation makes the comparison bytewise whatever the database default (§3.3).
SCOPE_PREFIX = (
    "(scope = %(scope)s OR (scope >= (%(scope)s || '/') COLLATE \"C\""
    " AND scope < (%(scope)s || '0') COLLATE \"C\"))"
)


class Store(Protocol):
    """What `CacheHook` needs from storage (design §4); `PgStore` is the implementation."""

    def get(self, key: str, now: datetime) -> Entry | None: ...

    def nearest(
        self, scope: str, partition: str, embedder: str, vector: list[float], now: datetime
    ) -> tuple[Entry, float] | None: ...

    def put_and_count(self, entry: Entry, now: datetime, day: date, *, spent: Usage) -> None: ...

    def count(
        self,
        day: date,
        scope: str,
        role: str,
        outcome: str,
        *,
        saved: Usage | None = None,
        spent: Usage | None = None,
    ) -> None: ...

    def invalidate(
        self, *, scope: str | None = None, role: str | None = None, key: str | None = None
    ) -> int: ...

    def stats(self, *, scope: str | None = None, since: date | None = None) -> list[RoleStats]: ...


class PgStore:
    """PostgreSQL store in one schema. Pooled connections are autocommit, with
    `search_path = <schema>, public` set once when the pool makes them, so a lookup is one round
    trip; only `put_and_count` opens a transaction. Every database error surfaces as `StoreError`
    (design §3.5)."""

    def __init__(self, pool: ConnectionPool, schema: str) -> None:
        self._pool = pool
        self._schema = schema
        self._last_purge: datetime | None = None

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection]:
        try:
            with self._pool.connection() as conn:
                yield conn
        except psycopg.Error as exc:
            raise StoreError(f"lite-cache store ({self._schema}): {exc}") from exc

    def get(self, key: str, now: datetime) -> Entry | None:
        """The entry under `key`, unless it is missing or expired at `now`."""
        with self._connection() as conn:
            row = (
                conn.cursor(row_factory=dict_row)
                .execute(
                    f"SELECT {', '.join(COLUMNS)} FROM entries WHERE key = %(key)s AND {UNEXPIRED}",
                    {"key": key, "now": now},
                )
                .fetchone()
            )
        return None if row is None else _entry(row)

    def nearest(
        self, scope: str, partition: str, embedder: str, vector: list[float], now: datetime
    ) -> tuple[Entry, float] | None:
        """The unexpired L2 entry in exactly this scope, partition and embedder whose question
        vector is closest (cosine) to `vector`, with its similarity; one query (design §2.4)."""
        with self._connection() as conn:
            row = (
                conn.cursor(row_factory=dict_row)
                .execute(
                    f"SELECT {', '.join(COLUMNS)}, "
                    "1 - (question_embedding <=> %(v)s::vector) AS similarity FROM entries "
                    "WHERE scope = %(scope)s AND partition = %(partition)s "
                    "AND embedder = %(embedder)s AND question_embedding IS NOT NULL "
                    f"AND {UNEXPIRED} ORDER BY similarity DESC LIMIT 1",
                    {
                        "v": vector,
                        "scope": scope,
                        "partition": partition,
                        "embedder": embedder,
                        "now": now,
                    },
                )
                .fetchone()
            )
        if row is None:
            return None
        similarity = float(row.pop("similarity"))
        return _entry(row), similarity

    def put(self, entry: Entry, now: datetime) -> None:
        """Write `entry`, replacing any entry under its key (no count: for tools and tests)."""
        with self._connection() as conn:
            _upsert(conn, entry)
        self._maybe_purge(now)

    def put_and_count(self, entry: Entry, now: datetime, day: date, *, spent: Usage) -> None:
        """Write `entry` and count it as `stored` in one transaction (§3.4)."""
        with self._connection() as conn, conn.transaction():
            _upsert(conn, entry)
            _count(conn, day, entry.scope, entry.role, "stored", None, spent)
        self._maybe_purge(now)

    def count(
        self,
        day: date,
        scope: str,
        role: str,
        outcome: str,
        *,
        saved: Usage | None = None,
        spent: Usage | None = None,
    ) -> None:
        """Add one call with `outcome` (`hit`, `stored`, `skip:<reason>`) and its tokens."""
        with self._connection() as conn:
            _count(conn, day, scope, role, outcome, saved, spent)

    def invalidate(
        self, *, scope: str | None = None, role: str | None = None, key: str | None = None
    ) -> int:
        """Delete the entries matching every given condition (scope by path prefix); returns how
        many. At least one condition is required."""
        if scope is None and role is None and key is None:
            raise ValueError("invalidate needs at least one of scope, role, key")
        where, params = _where(scope=scope, role=role, key=key)
        with self._connection() as conn:
            return conn.execute(f"DELETE FROM entries WHERE {where}", params).rowcount

    def purge_expired(self, now: datetime, limit: int | None = None) -> int:
        """Delete entries expired at `now` (at most `limit`); returns how many."""
        with self._connection() as conn:
            return conn.execute(
                "DELETE FROM entries WHERE key IN (SELECT key FROM entries "
                "WHERE expires_at <= %s ORDER BY expires_at LIMIT %s)",
                (now, limit),
            ).rowcount

    def stats(self, *, scope: str | None = None, since: date | None = None) -> list[RoleStats]:
        """Counts per role, for `scope` and everything under it, from `since` on (§3.4)."""
        where, params = _where(scope=scope, since=since)
        query = (
            "SELECT role, "
            "coalesce(sum(n) FILTER (WHERE outcome = 'hit'), 0) AS hits, "
            "coalesce(sum(n) FILTER (WHERE outcome = 'hit_semantic'), 0) AS semantic_hits, "
            "coalesce(sum(n) FILTER (WHERE outcome = 'stored'), 0) AS stored, "
            "coalesce(jsonb_object_agg(substr(outcome, 6), n) "
            "FILTER (WHERE outcome LIKE 'skip:%%'), '{}') AS skips, "
            f"{SUM_TOKENS} FROM (SELECT role, outcome, sum(n) AS n, {SUM_TOKENS} "
            f"FROM stats WHERE {where} GROUP BY role, outcome) per_outcome "
            "GROUP BY role ORDER BY role"
        )
        with self._connection() as conn:
            rows = conn.cursor(row_factory=dict_row).execute(query, params).fetchall()
        return [_role_stats(r) for r in rows]

    def _maybe_purge(self, now: datetime) -> None:
        """Opportunistic housekeeping after a write: its failure is logged, never raised, so a
        committed write is not reported as failed (§3.3)."""
        if self._last_purge is not None and now - self._last_purge < PURGE_EVERY:
            return
        self._last_purge = now
        try:
            self.purge_expired(now, PURGE_LIMIT)
        except StoreError as exc:
            log.warning("lite-cache could not purge expired entries: %s", exc)


def _where(**conditions: Any) -> tuple[str, dict[str, Any]]:
    """The AND of the given conditions (None = not given), with named parameters."""
    special = {"scope": SCOPE_PREFIX, "since": "day >= %(since)s"}
    given = {k: v for k, v in conditions.items() if v is not None}
    clauses = [special.get(name, f"{name} = %({name})s") for name in given]
    return " AND ".join(clauses or ["TRUE"]), given


def _upsert(conn: psycopg.Connection, e: Entry) -> None:
    u = e.usage
    conn.execute(
        UPSERT,
        (
            *(e.key, e.scope, e.role, e.backend, e.model, e.question, e.kind),
            *(Jsonb(e.payload), Jsonb(e.provenance)),
            *(u.input, u.output, u.reasoning, u.cost_usd, e.created_at, e.expires_at),
            *(e.partition, e.embedder, e.question_embedding),
        ),
    )


def _count(
    conn: psycopg.Connection,
    day: date,
    scope: str,
    role: str,
    outcome: str,
    saved: Usage | None,
    spent: Usage | None,
) -> None:
    saved, spent = saved or Usage(), spent or Usage()  # unknown token counts add 0
    tokens = (saved.input, saved.output, spent.input, spent.output)
    conn.execute(COUNT, (day, scope, role, outcome, *(t or 0 for t in tokens)))


def _entry(row: dict[str, Any]) -> Entry:
    usage = Usage(
        row.pop("input_tokens"),
        row.pop("output_tokens"),
        row.pop("reasoning_tokens"),
        row.pop("cost_usd"),
    )
    return Entry(**row, usage=usage)


def _role_stats(row: dict[str, Any]) -> RoleStats:
    return RoleStats(
        role=row["role"],
        hits=int(row["hits"]),
        stored=int(row["stored"]),
        skips={reason: int(n) for reason, n in row["skips"].items()},
        saved_input_tokens=int(row["saved_input_tokens"]),
        saved_output_tokens=int(row["saved_output_tokens"]),
        spent_input_tokens=int(row["spent_input_tokens"]),
        spent_output_tokens=int(row["spent_output_tokens"]),
        semantic_hits=int(row["semantic_hits"]),
    )


def _search_path(schema: str) -> Any:
    """Pool `configure` callback: the schema first, `public` for the vector type (S3)."""

    def configure(conn: psycopg.Connection) -> None:
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))

    return configure


def open_store(settings: Settings | None = None) -> PgStore:
    """The store for `settings` (default: from the environment). Checks the applied migrations
    without a lock and migrates only when behind (§4); open it once per process. The one-off
    connection fails fast with StoreError when the database is unreachable."""
    settings = settings or Settings()
    url = db.require_url(settings.database_url)
    with db.connect(url) as conn:
        if db.pending(conn, settings.schema_name):
            db.migrate(conn, settings.schema_name)
    pool = ConnectionPool(
        url,
        min_size=1,
        max_size=POOL_MAX,
        timeout=POOL_TIMEOUT,
        kwargs={"autocommit": True},
        configure=_search_path(settings.schema_name),
        open=True,
    )
    return PgStore(pool, settings.schema_name)
