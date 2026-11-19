# lite-cache

An LLM response cache for the lite modules. It plugs into
[lite-model](https://github.com/ArthurGao/lite-model) as a hook: when the same request comes again
(**L1**, exact: same scope, role, model, messages and parameters), the answer comes from PostgreSQL
instead of another model call, and the tokens it saved are counted. Semantic matching (**L2**) is
built but off: its calibration (2026-11-20, bge-m3) found no threshold that is both safe and useful,
so no embedder has a threshold and `CacheHook(embedder=…)` raises `ConfigError` (design §0, §8;
numbers in `kb/eval-results.md`).

Design and decisions: [`docs/lite-cache-design.md`](docs/lite-cache-design.md). License: Apache-2.0.

## Install

lite-cache is not on PyPI. Each release attaches a wheel to its
[GitHub Release](https://github.com/ArthurGao/lite-cache/releases); depend on it by URL:

```toml
[project]
dependencies = ["lite-cache"]

[tool.uv.sources]
lite-cache = { url = "https://github.com/ArthurGao/lite-cache/releases/download/v0.1.0/lite_cache-0.1.0-py3-none-any.whl" }
```

The wheel names its own lite-model (0.2.1, by URL), so it installs on its own. It needs
PostgreSQL with the pgvector extension available: the first migration runs
`CREATE EXTENSION IF NOT EXISTS vector` (L2 needs it later), so if the application's role may not
create extensions, run that once as a superuser in its database first.

## Quick start

```python
from lite_model import Message, build, call_context
from lite_cache import CacheHook, Policy, open_store

model = build(config, roles)                     # a lite_model.LiteModel
hook = CacheHook(open_store())                   # migrates the schema when behind
model.hooks.register(hook)

with call_context(scope="acme/u42", cache=Policy(ttl_s=86_400), question="What is the brew ratio?"):
    reply = model.text("answer", [Message("user", "...")])
reply.cached       # True when it came from the cache
reply.provenance   # always the model that originally answered
```

Open the store once per process. Caching is the call site's decision:

| `call_context(...)` | Meaning |
| --- | --- |
| `scope="acme/u42"` | **Required.** One or more `/`-separated `[A-Za-z0-9_.-]+` segments, coarse to fine. Two scopes never share an answer: answers personalised with a user's memories need a scope down to the user. |
| `cache=Policy(...)` or `cache=True` | **Required.** `True` is `Policy()`. Without it (or with `False`) nothing is read or written. |
| `question="..."` | Optional. The user's question as text, stored with the entry for inspection. Not part of the key. |

`Policy(ttl_s=None, accept=None, refresh=False)`: `ttl_s` is how long an entry lives (None: until
invalidated); `accept(reply)` returns None to store the reply or a reason to skip it (counted as
`skip:<reason>`); `refresh=True` skips the read, calls the model and overwrites.

What the key covers: scope, role, the configured backend and requested model, the messages and
every request parameter as sent (temperature, tools, response format, reasoning effort). Change
any of them and the old entry no longer matches. Do not pass a policy where the answer depends on
something outside the request (today's exchange rate), unless a short `ttl_s` is acceptable. Model
evaluations should pass none, so trials never share an answer.

## Failures

- **A read failure** raises `lite_cache.StoreError` and no request is sent: falling back to the
  model is the caller's decision.
- **A write or count failure after a paid call** never fails the call: it is logged (logger
  `lite_cache`) and counted in `hook.store_errors`, and the reply is returned.
- A stored entry that no longer fits (a schema whose validators changed, a row written by an
  older version) is a logged miss: the model is called and the entry overwritten.
- Truncated, empty and off-schema replies are lite-model errors: never stored, never counted.

## Invalidation and statistics

```python
hook.invalidate(scope="acme")            # this scope and everything under it (acme/...), not "acmex"
hook.invalidate(role="answer", key=...)  # conditions combine with AND
hook.stats(scope="acme", since=date(2026, 11, 1))  # per role: hits, stored, skips, tokens saved/spent
```

## CLI

Settings come from the environment or `.env`.

```
litecache migrate                         # apply pending migrations
litecache stats [--scope S] [--since YYYY-MM-DD]
litecache invalidate --scope S | --role R | --key K   # at least one; prints how many
litecache purge                           # delete every expired entry
litecache config                          # the settings, with the password redacted
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `LITECACHE_DATABASE_URL` | — | PostgreSQL URL (required) |
| `LITECACHE_SCHEMA` | `lite_cache` | lite-cache's own schema; not `public`, `information_schema` or `pg_*` |
| `LITECACHE_SEMANTIC_THRESHOLDS` | — | L2 (off, see above): `{"<embedder model_id>": <threshold>}` on top of the calibrated table shipped in the package; values only from a calibration run |
| `LITECACHE_TEST_DATABASE_URL` | — | tests only; they use the schema `lite_cache_test` |

## Development

```
uv sync
uv run --env-file .env pytest
uv run ruff check && uv run ruff format --check && uv run mypy
```

Tests need PostgreSQL with pgvector (`.env.example`); they never call a real model.
