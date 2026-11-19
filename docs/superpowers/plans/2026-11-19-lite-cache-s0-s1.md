# lite-cache S0–S1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working L1 (exact) LLM response cache that plugs into lite-model as a hook, stores entries and counters in PostgreSQL, and has a CLI — stages S0 and S1 of the design.

**Architecture:** `CacheHook` implements lite-model's sync `Hook` protocol: `before(call)` builds a key from the `Call` and returns a stored reply on a hit; `after(call, reply)` writes the fresh reply. All persistence goes through a `Store` Protocol whose only implementation is `PgStore` (psycopg pool, schema `lite_cache`, numbered SQL migrations run by a runner vendored from lite-rag). Canonical JSON is vendored from lite-eval.

**Tech Stack:** Python 3.12, uv (uv_build backend), lite-model 0.2.1 (release wheel), psycopg[binary,pool] 3.3.8, pydantic 2.14.3, pydantic-settings 2.16.2, Typer 0.27.3, pytest, ruff, mypy strict.

**Spec:** `docs/lite-cache-design.md` (§0 decisions, §2 data flow, §3 interface, §3.1–§3.5, §4 storage, §5 config, §6 CLI, §11 S0/S1 acceptance). Read it before any task.

## Global Constraints

- Repo `lite-cache`, package `lite_cache`, CLI `litecache`, env prefix `LITECACHE_`; Python `>=3.12,<3.13`; license Apache-2.0 (file exists).
- Run-time deps pinned exactly: `psycopg[binary,pool]==3.3.8`, `pydantic==2.14.3`, `pydantic-settings==2.16.2`, `typer==0.27.3`, and `lite-model` from `https://github.com/ArthurGao/lite-model/releases/download/v0.2.1/lite_model-0.2.1-py3-none-any.whl` (in `[tool.uv.sources]`, as lite-rag does). lite-model is the only sibling imported at run time (AGENTS.md rule 5).
- Dev group: `pytest>=8.3`, `ruff>=0.8`, `mypy>=1.13`. ruff/mypy/pytest config copied from lite-model's `pyproject.toml` (line-length 100; select E,F,W,I,UP,B,RUF; mypy strict + pydantic plugin, `files = ["src", "tests"]`).
- Vendored files keep a header naming the source repo, path and commit: lite-eval `backend/src/lite_eval/canonical.py` @ `76dd9a9`; lite-rag `src/lite_rag/db.py` @ `e42fc93` (blob identical at HEAD `a9ec093`). Allowed edits only: module/package names, the error class, env-var name in messages, the advisory-lock prefix `lite_cache:`; each edit listed in the header.
- Key content and version exactly as §3.1: `{"v": 2, "scope", "role", "backend", "model", "messages", "params"}`; `question` never in the key.
- Scope: one or more `/`-separated segments, each `^[A-Za-z0-9_.-]+$` (§3.1).
- `context["cache"]`: a `Policy` → that policy; `True` → `Policy()`; `False` or absent → no caching; anything else → `TypeError` (§3).
- Schema setting: field `schema_name`, alias `schema`, default `lite_cache`, pattern `^[a-z_][a-z0-9_]{0,62}$`, and not `public`, `information_schema`, `pg_*` (§5).
- Tests: PostgreSQL only via `LITECACHE_TEST_DATABASE_URL`, only schema `lite_cache_test`, dropped and recreated; never a real model or paid API; never llm-gateway (AGENTS.md rule 7). The env-isolation fixture removes every other `LITECACHE_*` variable and chdirs to a temp dir.
- Every new test is seen red once (written first, or the line under test broken and restored); say so in the task report.
- Commits: none without the owner's go-ahead (AGENTS.md Workflow 3). Each stage ends with the close-out in AGENTS.md Workflow 4 (`/simplify` → Codex review → fix → re-run checks) and then a *proposed* commit. Tasks below end with "checks green", not with a commit.
- Code-facing text in English; Conventional Commits.

## Review Focus

1. **The caller's `accept` predicate raises** → the paid reply must still be returned: the exception is logged, `store_errors` += 1, nothing written, nothing counted. (Task 6)
2. **A reply with unknown usage** (`Usage()` — all `None`, e.g. a backend that sends none) → counters add 0, a later hit reports `Usage()` again, nothing crashes. (Tasks 5, 6)
3. **The same text in decomposed vs composed Unicode** in messages → the same key (NFC), so a hit; different text never collides. (Task 3)
4. **Provenance round-trip on a hit**: `answered=None` (tag `backend:requested?`) and a non-empty `via` survive storage exactly. (Task 6)
5. **Two misses of the same request racing** (no single-flight) → both call the model, both `put`; one row remains, no error, two `stored` counts. (Task 4)

---

## File structure

```
pyproject.toml, uv.lock, .env.example, README.md (one paragraph; full README is S1.5)
.github/workflows/ci.yml
src/lite_cache/
  __init__.py      exports CacheHook, Policy, RoleStats, StoreError, open_store, Settings
  errors.py        LiteCacheError, StoreError, InvalidScope
  canonical.py     vendored (lite-eval)
  config.py        Settings
  db.py            vendored (lite-rag) + pending()
  migrations/__init__.py, 0001_initial.sql
  types.py         Policy, Entry, RoleStats
  keys.py          cache_key, check_scope, policy_from
  store.py         Store Protocol, PgStore, open_store
  hook.py          CacheHook
  cli.py           Typer app
tests/
  conftest.py      env isolation, pg_url, pg (empty lite_cache_test), store fixture
  upstream.py      copy of lite-model tests/upstream.py (header names source + commit)
  helpers.py       local_model(upstream) -> LiteModel, a_call(**overrides) -> Call
  unit/            test_canonical.py, test_config.py, test_keys.py
  contract/        test_migrations.py, test_store.py, test_stats.py, test_hook.py, test_cli.py
```

---

## Stage S0 — skeleton, CI, migrations

### Task 1: Project skeleton, local database, CI

**Files:**
- Create: `pyproject.toml`, `.env.example`, `README.md`, `.github/workflows/ci.yml`, `src/lite_cache/__init__.py`, `src/lite_cache/errors.py`, `tests/conftest.py`, `tests/contract/test_database.py`
- Modify: `.gitignore` (add `.env`, `.venv/`, caches if missing)

**Interfaces:**
- Produces: `errors.LiteCacheError(Exception)`, `errors.StoreError(LiteCacheError)`, `errors.InvalidScope(LiteCacheError, ValueError)`; fixtures `pg_url: str`, `pg: psycopg.Connection` (autocommit, schema `lite_cache_test` dropped before and after); constant `tests.conftest.TEST_SCHEMA = "lite_cache_test"`.

- [ ] **Step 1: Local database.** Check the shared container first (`docker ps --filter name=postgresql-postgres-1`). Create role `lite_cache` (random password) and database `lite_cache` owned by it, as lite-rag/lite-mem have (`docker exec postgresql-postgres-1 psql -U postgres …`; `CREATE EXTENSION vector` needs superuser: run it once as `postgres` in `lite_cache`). Write `LITECACHE_DATABASE_URL` and `LITECACHE_TEST_DATABASE_URL` (same DB) into `lite-cache/.env` (gitignored) and record the credential in `~/Docs/Knowledgebase/global/machine/credentials.local.md` (skill `dev-machine`/`secrets-hygiene`; never in a tracked file). `.env.example` mirrors lite-mem's with `CHANGE_ME`.
- [ ] **Step 2: `pyproject.toml`** per Global Constraints; `[project.scripts] litecache = "lite_cache.cli:app"` (the module arrives in Task 7: leave the entry, `cli.py` gets a stub `app = typer.Typer()` now). `uv sync`.
- [ ] **Step 3: Write the failing test** `tests/contract/test_database.py`:

```python
def test_the_test_database_has_a_non_c_default_collation(pg):
    # design §3.3: prefix invalidation is only proven if the column collation, not the DB's, does the work
    (collation,) = pg.execute("SELECT datcollate FROM pg_database WHERE datname = current_database()").fetchone()
    assert collation not in ("C", "POSIX")

def test_pgvector_is_installed(pg):
    assert pg.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone() is not None
```

- [ ] **Step 4: Run** `uv run --env-file .env pytest tests/contract/test_database.py -v` — fails (fixture `pg` missing).
- [ ] **Step 5: `tests/conftest.py`** — `_isolated_env` (autouse, as lite-rag's), `pg_url` (fails with "LITECACHE_TEST_DATABASE_URL is not set …" when unset), `pg` (drop/yield/drop `lite_cache_test`). `errors.py`.
- [ ] **Step 6: Run** the test again: PASS. Then `uv run ruff check && uv run ruff format --check && uv run mypy` green.
- [ ] **Step 7: CI** `.github/workflows/ci.yml` copied from lite-mem's `test` job only (no image job): service `pgvector/pgvector:0.8.6-pg18`, `POSTGRES_DB: lite_cache`, env `LITECACHE_TEST_DATABASE_URL`, steps `uv sync --locked`, ruff, ruff format, mypy, pytest. Note in a comment that the image's default locale is `en_US.utf8` (the collation test relies on it).

### Task 2: Vendored canonical JSON and migrations, config, `open_store` migrates

**Files:**
- Create: `src/lite_cache/canonical.py`, `src/lite_cache/db.py`, `src/lite_cache/config.py`, `src/lite_cache/migrations/__init__.py`, `src/lite_cache/migrations/0001_initial.sql`, `src/lite_cache/store.py` (only `open_store` + an empty `PgStore` shell in this task), `tests/unit/test_canonical.py`, `tests/unit/test_config.py`, `tests/contract/test_migrations.py`

**Interfaces:**
- Produces: `canonical.canonical_json(value) -> str`, `canonical.content_hash(value) -> str`; `db.connect`, `db.migrate(conn, schema, until=None) -> list[str]`, `db.pending(conn, schema) -> list[str]` (new: versions not yet applied; all of them when `schema_migrations` does not exist; reads without a lock), `db.redact_conninfo(value) -> str`, `db.use_schema(conn, schema)`; `config.Settings` (`database_url: str | None`, `schema_name: str` alias `schema`; `env_prefix="LITECACHE_"`, `env_file=".env"`, `extra="ignore"`, `populate_by_name=True`); `store.open_store(settings: Settings | None = None) -> PgStore` (runs `migrate` only when `pending` is non-empty; each process opens once).

- [ ] **Step 1: Failing tests.**
  - `test_canonical.py`: the two lite-eval vectors verbatim — `canonical_json({"b": [1, 2.5], "a": {"q": "é", "é": None}}) == '{"a":{"q":"é","é":null},"b":[1,2.5]}'`; `canonical_json({"x": math.nan})` raises `ValueError`; `content_hash({"a": 1}) == hashlib.sha256(b'{"a":1}').hexdigest()`.
  - `test_config.py`: default `schema_name == "lite_cache"`; `LITECACHE_SCHEMA=public`, `information_schema`, `pg_temp`, `Bad-Name`, `1abc` each raise `ValidationError`; `redact_conninfo("postgresql://u:secret@h/db")` contains `***` and not `secret`.
  - `test_migrations.py`: `migrate(pg, TEST_SCHEMA)` returns `["0001_initial"]`, a second call returns `[]`; `pending(pg, TEST_SCHEMA)` is `["0001_initial"]` before and `[]` after; tables `entries` and `stats` exist in `lite_cache_test`; `entries.scope` collation is `C` (query `information_schema.columns.collation_name`); `open_store(Settings(database_url=pg_url, schema="lite_cache_test"))` on a fresh schema leaves `pending == []`.
- [ ] **Step 2: Run** them: fail (modules missing).
- [ ] **Step 3: Implement.** Vendor the two files (header per Global Constraints). `0001_initial.sql` — the §4 schema exactly:

```sql
CREATE TABLE entries (
  key text PRIMARY KEY,
  scope text COLLATE "C" NOT NULL,
  role text NOT NULL, backend text NOT NULL, model text NOT NULL,
  question text,
  kind text NOT NULL CHECK (kind IN ('text', 'structured')),
  payload jsonb NOT NULL, provenance jsonb NOT NULL,
  input_tokens integer, output_tokens integer, reasoning_tokens integer, cost_usd double precision,
  created_at timestamptz NOT NULL, expires_at timestamptz
);
CREATE INDEX entries_scope ON entries (scope COLLATE "C");
CREATE INDEX entries_role ON entries (role);
CREATE INDEX entries_expires ON entries (expires_at) WHERE expires_at IS NOT NULL;
CREATE TABLE stats (
  day date NOT NULL, scope text COLLATE "C" NOT NULL, role text NOT NULL, outcome text NOT NULL,
  n bigint NOT NULL DEFAULT 0,
  saved_input_tokens bigint NOT NULL DEFAULT 0, saved_output_tokens bigint NOT NULL DEFAULT 0,
  spent_input_tokens bigint NOT NULL DEFAULT 0, spent_output_tokens bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (day, scope, role, outcome)
);
```

- [ ] **Step 4: Run** all tests and `ruff`/`mypy`: green.

### S0 close-out

- [ ] Run AGENTS.md Workflow step 4 on the S0 diff (`/simplify` via the Skill tool; Codex `review --wait --scope working-tree`; fix or write down rejected findings; re-run every check). Report checks + findings; **propose** the commit `chore: S0 skeleton, CI, migrations` with the file list, and wait for the owner. After the push, read CI (`gh run list --limit 1 --json conclusion`).

---

## Stage S1 — L1

### Task 3: Keys, scope and policy parsing

**Files:**
- Create: `src/lite_cache/types.py` (`Policy`), `src/lite_cache/keys.py`, `tests/helpers.py`, `tests/upstream.py`, `tests/unit/test_keys.py`

**Interfaces:**
- Consumes: `lite_model.hooks.Call`, `canonical.canonical_json`, `errors.InvalidScope`.
- Produces:
  - `types.Policy` — frozen dataclass `(ttl_s: int | None = None, accept: Callable[[Reply], str | None] | None = None, refresh: bool = False)`; `ttl_s <= 0` raises `ValueError`.
  - `keys.KEY_VERSION = 2`; `keys.check_scope(scope: object) -> str` (raises `InvalidScope`); `keys.policy_from(context: Mapping[str, Any]) -> Policy | None` (rules in Global Constraints); `keys.cache_key(call: Call, scope: str) -> str` = `content_hash({"v": 2, "scope", "role": call.role, "backend": call.target.backend, "model": call.target.model, "messages": [[m.role, m.content] …], "params": dict(call.params)})`.
  - `tests.helpers.local_model(up: Upstream, **config) -> LiteModel` — `build(ModelsConfig.model_validate({"backend": "ollama", "model": "qwen3:8b"} | config), RoleSpec({"answer": RolePolicy(), "graph": RolePolicy()}), http_client=up.client())`; `tests.helpers.a_call(**changes) -> Call` — a `Call` for role `answer` built from `local_model(...).target("answer")`, messages `(Message("user", "Hi?"),)`, request `{"model": "qwen3:8b", "messages": [...], "temperature": 0}`, with `dataclasses.replace` on the target / call for the changes.

- [ ] **Step 1: Failing tests** (`test_keys.py`):
  - `test_the_key_is_stable` — same call twice → same key; it is 64 hex chars.
  - `test_each_part_changes_the_key` (parametrised) — scope `acme` vs `acme/u1`; role `answer` vs `graph`; target backend `ollama` vs `deepseek`; target model `qwen3:8b` vs `qwen3:1.7b`; messages content `Hi?` vs `Hi!`; a message role `user` vs `system`; params each of `temperature 0→0.5`, add `tools=[{…}]`, add `response_format={…}`, add `reasoning_effort="low"` → key differs from the base.
  - `test_question_is_not_in_the_key` — `call_context(question=…)` differences → same key.
  - `test_decomposed_and_composed_text_share_a_key` (Review Focus 3) — content `"café"` vs `"café"` → same key; `"cafe"` → different.
  - `test_nan_in_params_raises` — `temperature=math.nan` → `ValueError`.
  - `test_scope_format` — accepts `acme`, `acme/u42`, `tenderted/tenant_42/faq/u.1`; rejects `""`, `"a//b"`, `"/a"`, `"a/"`, `"a b"`, `"ä"`, `None`, `3` with `InvalidScope`.
  - `test_policy_from_context` — `{}` → None; `{"cache": False}` → None; `{"cache": True}` → `Policy()`; `{"cache": Policy(ttl_s=60)}` → that object; `{"cache": "yes"}` and `{"cache": 1}` → `TypeError`; `Policy(ttl_s=0)` → `ValueError`.
- [ ] **Step 2: Run:** fail. **Step 3: Implement.** **Step 4: Run** + ruff + mypy: green.

### Task 4: `PgStore` — entries, TTL, invalidation

**Files:**
- Modify: `src/lite_cache/types.py` (add `Entry`), `src/lite_cache/store.py`
- Test: `tests/contract/test_store.py`; `tests/conftest.py` gains fixture `store` → `PgStore` on a freshly migrated `lite_cache_test`

**Interfaces:**
- Produces:
  - `types.Entry` — frozen dataclass `(key, scope, role, backend, model, question: str | None, kind: Literal["text","structured"], payload: Any, provenance: dict[str, Any], usage: Usage, created_at: datetime, expires_at: datetime | None)`.
  - `store.Store` Protocol and `PgStore(pool: ConnectionPool, schema: str)` with: `get(key: str, now: datetime) -> Entry | None` (expired → None); `put(entry: Entry, now: datetime) -> None` (upsert on `key`, replaces the row; at most once per hour per instance also runs `purge_expired(now, limit=1000)`); `invalidate(*, scope: str | None = None, role: str | None = None, key: str | None = None) -> int` (≥ 1 condition else `ValueError`; conditions ANDed; scope by path prefix with exactly the §3.3 SQL `scope = %s OR (scope >= %s || '/' AND scope < %s || '0')` on the C-collated column); `purge_expired(now: datetime, limit: int | None = None) -> int`.
  - Every psycopg error inside the store becomes `StoreError` (chained). Each operation is one transaction with `use_schema` first.

- [ ] **Step 1: Failing tests** (`test_store.py`), all with explicit `now`:
  - `test_put_then_get_round_trips_every_field` — including `question=None`, `usage=Usage()` and `cost_usd` present/absent.
  - `test_an_expired_entry_is_not_returned` — `expires_at = now + 10s`; `get(now + 11s)` → None; `get(now + 9s)` → entry.
  - `test_put_overwrites_the_same_key` (Review Focus 5) — two `put`s of one key from two threads → one row, no error.
  - `test_scope_prefix_invalidation` — rows with scopes `a`, `a/x.y`, `a/b/c`, `ab`, `a_b`, `a.b`; `invalidate(scope="a")` returns 3 and leaves exactly `ab`, `a_b`, `a.b`.
  - `test_invalidate_by_role_and_key_and_intersection` — role only, key only, scope+role intersection counts; no condition → `ValueError`.
  - `test_purge_removes_only_expired_and_respects_the_limit`; `test_put_purges_at_most_once_an_hour` (second `put` within the hour leaves an expired row, one after `now + 1h` removes it).
  - `test_a_database_error_is_a_store_error` — a `PgStore` on a dropped schema → `get` raises `StoreError`.
- [ ] **Step 2–4:** run (fail) → implement → run + ruff + mypy (green).

### Task 5: Counting and `stats`

**Files:**
- Modify: `src/lite_cache/types.py` (add `RoleStats`), `src/lite_cache/store.py`
- Test: `tests/contract/test_stats.py`

**Interfaces:**
- Produces:
  - `Store.count(day: date, scope: str, role: str, outcome: str, *, saved: Usage | None = None, spent: Usage | None = None) -> None` — one upsert `INSERT … ON CONFLICT (day, scope, role, outcome) DO UPDATE SET n = stats.n + 1, … += …`; `None` token counts add 0.
  - `Store.put_and_count(entry, now, day, outcome="stored", spent: Usage) -> None` — the `stored` write and its count in **one** transaction (§3.4).
  - `Store.stats(*, scope: str | None = None, since: date | None = None) -> list[RoleStats]` — scope by the same prefix rule; grouped by role.
  - `types.RoleStats` — frozen `(role: str, hits: int, stored: int, skips: dict[str, int], saved_input_tokens: int, saved_output_tokens: int, spent_input_tokens: int, spent_output_tokens: int)` with property `hit_rate -> float | None` = `hits / (hits + stored + sum(skips))`, None when the denominator is 0.

- [ ] **Step 1: Failing tests:**
  - `test_counts_add_up_per_outcome` — 2×hit (saved 100/20 each), 1×stored (spent 100/20), 1×`skip:empty` → one `RoleStats` with `hits=2, stored=1, skips={"empty": 1}`, saved 200/40, spent 100/20, `hit_rate == 0.5`.
  - `test_unknown_usage_counts_as_zero` (Review Focus 2) — `spent=Usage()` → token sums 0, `n` incremented.
  - `test_concurrent_upserts_lose_nothing` — 8 threads × 25 `count(...)` on one key → `hits == 200`.
  - `test_stats_filters_by_scope_prefix_and_since` — scopes `a/u1`, `ab`; days D-1, D → `stats(scope="a", since=D)` only counts `a/u1` on D.
  - `test_put_and_count_is_atomic` — a test-only `PgStore` subclass overrides the private count statement with invalid SQL → `put_and_count` raises `StoreError`, and afterwards `get(key)` is None and `stats()` is empty (the entry was rolled back with the count).
- [ ] **Step 2–4:** run (fail) → implement → run + ruff + mypy (green).

### Task 6: `CacheHook`

**Files:**
- Create: `src/lite_cache/hook.py`
- Modify: `src/lite_cache/__init__.py` (exports)
- Test: `tests/contract/test_hook.py`

**Interfaces:**
- Consumes: `keys.cache_key/check_scope/policy_from`, `Store` (Tasks 4–5), `lite_model.hooks.Call`, `lite_model.{TextReply, StructuredReply, Provenance, Usage}`.
- Produces: `CacheHook(store: Store, clock: Callable[[], datetime] = lambda: datetime.now(UTC))` with `name = "lite-cache"`, `before(call) -> Reply | None`, `after(call, reply) -> None`, `invalidate(**kw) -> int` (delegates), `stats(**kw) -> list[RoleStats]` (delegates), attribute `store_errors: int`. Logger `logging.getLogger("lite_cache")`. Stats day = `clock().date()` (UTC).

Behaviour (§2, §3, §3.5) the tests pin:
- `before`: no policy or no scope in `call.context` → None, no store access. Otherwise `check_scope`, key; `refresh=True` → None (after will write). `get` raising → `StoreError` propagates (lite-model sends nothing). On an entry whose `kind` matches the call (`schema is None` ↔ `text`): rebuild `TextReply(text, Provenance(**p), usage)` or `StructuredReply(call.schema.model_validate(payload), …)`; a `ValidationError` there → warning, return None (miss; the fresh reply overwrites). Then `count(hit, saved=entry.usage)`; a failure there → warning, `store_errors += 1`, the hit is still returned.
- `after`: same gating; `accept(reply)`: `None` → `put_and_count(stored, spent=reply.usage)` with `question = context.get("question")`, `expires_at = now + ttl_s`; a string `r` → `count("skip:" + r, spent=…)`. Any exception in `accept`, `put_and_count` or `count` → warning, `store_errors += 1`, never raised (Review Focus 1).
- Provenance stored with `dataclasses.asdict`; `via` round-trips as a mapping.

- [ ] **Step 1: Failing tests** (`test_hook.py`; a real `LiteModel` from `tests.helpers.local_model`, `Upstream` scripting completions; hook on the `store` fixture; a fixed `clock`):
  - `test_the_second_identical_call_sends_nothing` — two `model.text("answer", ASK)` inside `call_context(scope="acme", cache=True)`: `len(up.requests) == 1`; second reply `cached is True`, same `text`, `provenance` and `usage` as the first; stats `hits == 1, stored == 1`.
  - `test_without_scope_or_policy_nothing_is_read_or_written` — three cases (no context; scope only; cache only) → 2 requests each, `entries` table empty, no stats rows.
  - `test_question_is_stored_but_does_not_change_the_key` — two calls with different `question` → 1 request; the row's `question` is the first one's.
  - `test_structured_hit_restores_the_schema` — `model.structured("answer", ASK, City)` twice → second `value == City(name="Auckland")`, `cached`.
  - `test_a_structured_entry_that_no_longer_validates_is_a_miss` — first call with `City`, second with a same-named class with the same JSON schema but a `field_validator` rejecting the stored value → second sends a request, `cached is False`, the row is overwritten.
  - `test_accept_refusal_is_counted_and_not_stored` — `Policy(accept=lambda r: "too_short")` → no row, stats `skips == {"too_short": 1}`.
  - `test_accept_that_raises_still_returns_the_reply` (Review Focus 1) — reply returned, no row, `hook.store_errors == 1`.
  - `test_truncated_and_empty_replies_are_neither_stored_nor_counted` — upstream `finish="length"` → `Truncated`; content `""` → `EmptyReply`; table and stats empty.
  - `test_an_expired_entry_is_called_again` — `Policy(ttl_s=60)`, clock advanced 61 s → 2 requests.
  - `test_refresh_skips_the_read_and_overwrites` — `Policy(refresh=True)` → 2 requests, stats `stored == 2`.
  - `test_unknown_usage_and_provenance_round_trip` (Review Focus 2, 4) — `store.put` an `Entry` under the exact key `cache_key(call, "acme")` of the next call, with provenance `answered=None`, `via={"x-litellm-model-id": "m1"}` and `usage=Usage()`; then `model.text(...)` in that context → no request, `reply.provenance` equals the stored one field for field, `reply.provenance.tag.endswith("?")`, `reply.usage == Usage()`, and the stats row adds 0 saved tokens.
  - `test_a_read_failure_raises_and_sends_nothing` — a `Store` stub whose `get` raises `StoreError` → `model.text` raises `StoreError`, `up.requests == []`.
  - `test_write_and_count_failures_never_fail_the_call` — stub `put_and_count` / `count` raising → reply returned, `store_errors` counts each; a hit whose `count` fails still returns `cached=True`.
  - `test_invalid_scope_fails_before_sending` — `scope="a b"` → `InvalidScope`, no request.
  - `test_two_scopes_never_share` — same call under `acme/u1` then `acme/u2` → 2 requests.
- [ ] **Step 2–4:** run (fail) → implement → run + ruff + mypy (green).

### Task 7: CLI

**Files:**
- Modify: `src/lite_cache/cli.py`
- Test: `tests/contract/test_cli.py` (Typer `CliRunner`, env `LITECACHE_DATABASE_URL=<pg_url>`, `LITECACHE_SCHEMA=lite_cache_test`)

**Interfaces:**
- Produces commands (§6): `migrate` (prints applied versions or "up to date"); `stats [--scope S] [--since YYYY-MM-DD]` (one line per role: hits, stored, skips, hit rate, saved/spent tokens); `invalidate (--scope|--role|--key …)` (≥ 1 option, prints the count; none → exit code 2); `purge` (all expired; prints the count); `config` (the assembled settings, URL through `redact_conninfo`). Errors → message on stderr, exit 1, no traceback.

- [ ] **Step 1: Failing tests:** `migrate` twice ("0001_initial" then "up to date"); `invalidate` without options exits 2; `invalidate --scope a` prints the deleted count; `config` output contains `***` and not the password; `stats --since not-a-date` exits non-zero with a message; an unreachable URL → exit 1, stderr names the problem, no traceback.
- [ ] **Step 2–4:** run (fail) → implement → run + ruff + mypy (green).

### Task 8: S1 close-out

- [ ] Update the design's status line (S0–S1 done, date) and any section the code changed; add traps met to `kb/lessons.md` (create it, per `kb/README.md`).
- [ ] Fix the stale sentence in `lite-model/docs/lite-model-design.md` §10 ("lite-cache 写失败时照样返回，带 `store_error`") to say the reply has no such field and failures show in the log and `CacheHook.store_errors` — separate repo: propose its own docs commit.
- [ ] Run the full suite: `uv run --env-file .env pytest` and `uv run ruff check && uv run ruff format --check && uv run mypy` (read the summary lines).
- [ ] AGENTS.md Workflow step 4 on the S1 diff (`/simplify`, Codex review, fix, re-run). Report every check, Codex's findings and what was fixed/rejected; **propose** the commit `feat(cache): S1 L1 cache — key, CacheHook, PgStore, stats, CLI`, wait for the owner, then check CI after the push.
