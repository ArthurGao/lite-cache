# lite-cache — Agent Guide

lite-cache is an **LLM response cache** library: a repeated question (L1, exact) — and from S3 a
sufficiently similar one (L2, semantic) — is answered from the cache instead of spending tokens on
another model call. It plugs into lite-model as a hook (every lite module calls models through
lite-model); lite-rag's `ask()` is its first consumer (S4). Storage is PostgreSQL; not Redis, not the
LiteLLM Proxy cache (design §0, 2026-11-19).

This file is the shared guide for every coding agent. `CLAUDE.md` imports it.

Naming: repo `lite-cache`, Python package `lite_cache`, CLI `litecache`, env prefix `LITECACHE_`.

## Sources of truth (highest first)

1. `docs/lite-cache-design.md` — the design. Its first line is the project status; §0 the owner's
   decisions; §11 the stages and their acceptance. Read §11 before starting work.
2. `kb/` in this repo — findings, eval numbers, lessons. What goes where: `kb/README.md`.

If a change must contradict the design, update the design in the same change and say why in the commit.

## Rules

1. **Scope is mandatory and part of every key** (design §3.1): two scopes never share an answer.
2. **The cache does not interpret content**: the payload is opaque, lite-model raises on a truncated,
   empty or off-schema reply before the cache sees it, the write rule is a replaceable predicate
   (design §3.2).
3. **Caching is the call site's decision**: no policy (or no scope) in `call_context`, no caching; model
   evals pass none, so trials never share an answer. L2 thresholds come from a
   lite-eval run (design §8), never a guess.
4. **Failures are visible**: a read failure raises `StoreError` and no request is sent; a write failure
   after a paid call never raises (lite-model would drop the reply): a warning and `store_errors`
   (design §3.5). A hit always carries the original provenance and usage, `cached=True`.
5. **lite-model is the only run-time dependency among the sibling projects** (its hook protocol and
   types). Other code taken from siblings is vendored, with the source and commit in the file header
   and a test vector compared with the source. lite-eval is only an `eval` extra and a dev dependency,
   for evaluation (design §0).
6. **Every component is a Protocol** (`Store`, later `Embedder`); the cache depends on Protocols only.
7. **Tests never call a real model or a paid API.** PostgreSQL tests use `LITECACHE_TEST_DATABASE_URL`
   and only the schema `lite_cache_test`. Never call llm-gateway subscription profiles.
8. **Pinned engine dependencies** (`psycopg`), exact versions.

## Tech stack

Python 3.12, uv, pydantic / pydantic-settings, Typer, PostgreSQL + pgvector (psycopg), pytest, ruff,
mypy (strict).

## Commands (from S0)

```
uv run pytest
uv run ruff check && uv run ruff format --check && uv run mypy
uv run litecache migrate | stats | invalidate | purge | config
```

## Workflow

1. Work stage by stage (design §11). Each stage ends with every check green; every new test seen red
   once (write it first, or break the line under test and watch it fail).
2. Definition of done: ruff, mypy, tests pass; acceptance items covered by tests; design updated if
   behaviour changed.
3. Conventional Commits (`feat(cache): …`). All code-facing text and docs (design doc, README, plans, comments) in English; Chinese only as examples or test data. No commit without the
   owner's go-ahead for that commit.
4. **Stage close-out — every stage, after its code passes the checks, before its commit and before the
   next stage starts:**
   1. **Simplify**: run `/simplify` on the stage's full diff and apply its fixes.
   2. **Codex review**, read-only, on the same diff, from the repo root:
      ```
      node "$(ls -d ~/.claude/plugins/cache/openai-codex/codex/*/ | sort -V | tail -1)/scripts/codex-companion.mjs" \
        review --wait --scope working-tree        # or --base <commit before the stage>
      ```
      For focus text use `adversarial-review --wait --scope working-tree "<focus>"`. Fallback without
      the plugin: `codex exec -s read-only -o <out.md> "<prompt>" < /dev/null` — the `< /dev/null`
      matters: with a piped stdin `codex exec` waits for stdin to close and hangs in a background shell.
      If Codex is unavailable, say so; never skip silently.
   3. **Fix what holds up**: verify each finding before acting; a bug gets a failing regression test
      first. A rejected finding is written down with a one-line reason, never silently dropped.
   4. **Re-run every check** (step 2) so they cover the final code. If step 3 changed code
      substantially, run steps 1 and 2 once more.
   5. Report the checks' output, Codex's findings and what was fixed or rejected; propose the commit.
