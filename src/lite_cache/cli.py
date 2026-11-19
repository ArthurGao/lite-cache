"""The `litecache` CLI (design §6): migrate, stats, invalidate, purge, config.

Settings come from the environment / `.env` (§5). An error is one line on stderr and exit 1."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated

import typer

from lite_cache import db
from lite_cache.config import Settings
from lite_cache.errors import LiteCacheError
from lite_cache.store import PgStore, open_store

app = typer.Typer(no_args_is_help=True, help="lite-cache: an LLM response cache (design §6).")


@contextmanager
def _errors() -> Iterator[None]:
    """lite-cache's own errors as a message and exit 1, never a traceback."""
    try:
        yield
    except LiteCacheError as exc:
        typer.echo(f"litecache: {exc}", err=True)
        raise typer.Exit(1) from None


def _with_store[T](action: Callable[[PgStore], T]) -> T:
    with _errors():
        store = open_store()
        try:
            return action(store)
        finally:
            store.close()


@app.command()
def migrate() -> None:
    """Apply pending migrations to the configured schema."""
    settings = Settings()
    with _errors(), db.connect(db.require_url(settings.database_url)) as conn:
        applied = db.migrate(conn, settings.schema_name)
    typer.echo("\n".join(applied) if applied else "up to date")


@app.command()
def stats(
    scope: Annotated[str | None, typer.Option(help="This scope and everything under it.")] = None,
    since: Annotated[
        datetime | None, typer.Option(formats=["%Y-%m-%d"], help="From this day on (UTC).")
    ] = None,
) -> None:
    """Hits, stores, skips and tokens per role."""
    rows = _with_store(lambda s: s.stats(scope=scope, since=since.date() if since else None))
    for r in rows:
        rate = "-" if r.hit_rate is None else f"{r.hit_rate:.2f}"
        parts = [r.role, f"hits {r.hits}", f"stored {r.stored}"]
        parts += [f"skip:{reason} {n}" for reason, n in sorted(r.skips.items())]
        parts += [
            f"hit_rate {rate}",
            f"saved {r.saved_input_tokens}/{r.saved_output_tokens}",
            f"spent {r.spent_input_tokens}/{r.spent_output_tokens}",
        ]
        typer.echo(" ".join(parts))


@app.command()
def invalidate(
    scope: Annotated[str | None, typer.Option(help="This scope and everything under it.")] = None,
    role: Annotated[str | None, typer.Option()] = None,
    key: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Delete the entries matching every given condition; prints how many."""
    if scope is None and role is None and key is None:
        raise typer.BadParameter("give at least one of --scope, --role, --key")
    typer.echo(_with_store(lambda s: s.invalidate(scope=scope, role=role, key=key)))


@app.command()
def purge() -> None:
    """Delete every expired entry; prints how many."""
    typer.echo(_with_store(lambda s: s.purge_expired(datetime.now(UTC))))


@app.command()
def config() -> None:
    """The assembled settings, with the database password redacted."""
    settings = Settings()
    url = db.redact_conninfo(settings.database_url) if settings.database_url else "(not set)"
    typer.echo(f"database_url {url}\nschema {settings.schema_name}")
