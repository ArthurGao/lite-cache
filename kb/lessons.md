# Lessons

Traps met while building lite-cache, each with the check that now catches it.

## pydantic-settings reads a plain `alias` without the env prefix (S0, 2026-11-19, measured)

`schema_name: str = Field("lite_cache", alias="schema")` on a top-level `BaseSettings` with
`env_prefix="LITECACHE_"` read the **unprefixed** `SCHEMA` variable and ignored `LITECACHE_SCHEMA`.
lite-rag's same-looking field works only because it sits in a nested model. Fix:
`validation_alias="LITECACHE_SCHEMA"`. Check: `tests/unit/test_config.py`
(`test_an_unprefixed_schema_variable_is_ignored`; with the old alias, 7 config tests go red).

## The key tells `0` from `0.0`; a Python `==` does not (S1, 2026-11-19, measured)

lite-model sends `temperature: 0.0`. A hand-built test `Call` with `temperature: 0` produced a
different key (canonical JSON writes `0` and `0.0`), so a hit test missed. A first drift test
compared the two requests with `dict ==` and passed anyway, because `0 == 0.0` in Python. Check:
`test_the_hand_made_call_matches_what_lite_model_sends` compares them as canonical JSON (seen red
with `temperature: 0`). Production is unaffected: lite-model builds requests deterministically.

## Prefix invalidation needs the C collation, on the column (S1, 2026-11-19, measured)

Without `COLLATE "C"`, on a database whose default collation is `en_US.utf8`,
`invalidate(scope="a")` over `a`, `a/x.y`, `a/b/c`, `ab`, `a_b`, `a.b` deleted 1 row instead of 3.
Check: `tests/contract/test_store.py::test_scope_prefix_invalidation`, run against a database whose
default collation is not C (`tests/contract/test_database.py` asserts that precondition).

## A zsh `$2` is not split into words (S1, 2026-11-19, observed)

A mutation helper `mut() { pytest $2; }` passed `"file -k expr"` as one argument in zsh, every run
errored, and the output looked like "nothing failed". Mutation runs now inline the full command.
