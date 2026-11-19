# lite-cache S3 — L2 semantic cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A call that opts in (`Policy(semantic=True)` with `question` and `partition`) is answered from a cached entry of a *similar* question in the same scope and partition — only when the similarity is above a threshold calibrated on labelled question pairs and a guard finds no difference in numbers or negation — with the false-hit rate held under the design §8 gate.

**Architecture:** S3a adds the mechanism: an injected `Embedder` Protocol, a migration with `partition` / `embedder` / `question_embedding` columns, an exact cosine scan within `(scope, partition, embedder)` in SQL, a guard, `hit_semantic` counting, and per-embedder thresholds from a packaged `thresholds.json` (empty until S3b). S3b measures: PAWS (en), PAWS-X (zh) and a generated domain set run through lite-eval (the target outputs cosine and the guard's verdict, nothing decided there); a calibration script sweeps thresholds on dev and gates on test with Wilson bounds, then writes the threshold.

**Tech Stack:** Python 3.12, lite-cache 0.1.x, lite-model 0.2.1, PostgreSQL + pgvector 0.8, lite-eval 0.4.1 (`eval` extra), Ollama `bge-m3:567m` (1024-d, no prefixes) for S3b only.

**Spec:** `docs/lite-cache-design.md` §0 (2026-11-19 rows), §2 step 4, §3 (L2, Embedder Protocol), §3.4 (`hit_semantic`), §4 (S3 columns), §5 (`LITECACHE_SEMANTIC_THRESHOLDS`), §8 (pairs, gate), §11 S3 acceptance.

## Global Constraints

- L2 runs only when `Policy.semantic` is true **and** `call.context` has both `question` (str) and `partition` (str); otherwise behaviour is exactly S1's (L1 only).
- `semantic=True` with no embedder on the hook, or no threshold for `embedder.model_id`, raises `lite_cache.ConfigError` from `before` — no request is sent (design §0).
- Thresholds come only from calibration runs: packaged `src/lite_cache/thresholds.json` (`{}` until S3b), overridable by `LITECACHE_SEMANTIC_THRESHOLDS` (JSON object); never a guessed number in code.
- Exact cosine scan in SQL within `(scope, partition, embedder)`, no ANN index (design §0); the scope filter is the C-collated equality (not prefix).
- The vector computed in `before` is the one written in `after` (no second embed).
- Key, L1 behaviour, failure semantics (§3.5) unchanged; a read failure (including the semantic lookup and the embedder) raises before sending; write failures never raise.
- S3b data: datasets downloaded to a temp dir per run, sha256 recorded in `kb/eval-results.md`, never committed; the generated set is ours and is committed (`eval/pairs/generated.jsonl`). Generation uses the local Ollama, never subscription channels.
- Gate (each test set separately): ≥ 300 negatives, ≥ half of them hard; false-hit rate ≤ 0.01 and its 95 % Wilson upper bound ≤ 0.02. Thresholds are chosen on dev only.
- AGENTS.md workflow: TDD (every new test seen red), stage close-out per sub-stage (`/simplify` → Codex → fix → re-run), commits with the owner's go-ahead.

## Review Focus

1. **The two questions are identical** (L1 key differs only through other params, e.g. different retrieved text in the same partition) → L2 may hit; cosine 1.0 must not overflow the threshold logic.
2. **Questions in different scripts / languages with the same meaning** (bge-m3 is multilingual) → allowed to hit if similar enough and the guard passes; numbers written as digits vs words (`3` vs `三`) → the guard treats unparsed number words as no-number; document that limitation.
3. **A partition with many entries** (hundreds) → still one SQL query with `ORDER BY distance LIMIT 1`; no Python-side loop over rows.
4. **The embedder raises or returns the wrong dimension** → a read failure (`StoreError`/`ConfigError`), no request sent, no partial write.
5. **An L2 hit whose stored entry is structured but the call is text** (or the schema no longer validates) → a miss, as in S1, not a crash.

---

## S3a — mechanism

### Task 1: Policy, Embedder Protocol, thresholds

**Files:** Modify `src/lite_cache/types.py`, `src/lite_cache/errors.py`; Create `src/lite_cache/semantic.py`, `src/lite_cache/thresholds.json`; Test `tests/unit/test_semantic.py`

**Interfaces — Produces:**
- `Policy(..., semantic: bool = False)`.
- `errors.ConfigError(LiteCacheError)`.
- `semantic.Embedder` Protocol: `model_id: str`, `dim: int`, `embed_query(text: str) -> list[float]`.
- `semantic.thresholds() -> dict[str, float]` — packaged JSON merged with `LITECACHE_SEMANTIC_THRESHOLDS` (env wins per key); values must be in (0, 1], else `ConfigError`.
- `semantic.guard(q1: str, q2: str) -> str | None` — None when the questions may share an answer; else a reason (`"numbers"`, `"negation"`). Numbers: the multiset of digit runs (`\d+(?:[.,]\d+)*`, normalised) must be equal. Negation: the presence of a negator must agree — en `\b(not|no|never|none|nobody|nothing|without)\b|n't`, zh `不|没|未|非|别|无|勿`. (Entities are decided in S3b from dev data, not guessed here.)

- [ ] Failing tests: `Policy(semantic=True)` default False; thresholds: empty packaged + env `{"m": 0.9}` → `{"m": 0.9}`, env value `1.5` or `0` → ConfigError, malformed JSON → ConfigError; guard table: ("退款要几天", "退款需要多少天") → None; ("超过1000要审批吗", "超过2000要审批吗") → "numbers"; ("能退货吗", "不能退货吗") → "negation"; ("Can I return it?", "Can't I return it?") → "negation"; ("refund 1,000", "refund 1000") → None (normalised); identical → None.
- [ ] Red → implement → green; ruff, mypy.

### Task 2: Migration and the semantic lookup in `PgStore`

**Files:** Create `src/lite_cache/migrations/0002_semantic.sql`; Modify `src/lite_cache/types.py` (`Entry` gains `partition: str | None = None`, `embedder: str | None = None`, `question_embedding: list[float] | None = None`), `src/lite_cache/store.py`; Test `tests/contract/test_semantic_store.py`

**Interfaces — Produces:**
- Migration: `ALTER TABLE entries ADD partition text, ADD embedder text, ADD question_embedding vector;` index `entries_semantic ON entries (scope COLLATE "C", partition, embedder) WHERE question_embedding IS NOT NULL`.
- `PgStore.nearest(scope: str, partition: str, embedder: str, vector: list[float], now: datetime) -> tuple[Entry, float] | None` — the unexpired entry with the highest cosine (`1 - (question_embedding <=> %s::vector)`), its similarity; None when none. One query, `ORDER BY … LIMIT 1`.
- `Store` Protocol gains `nearest`; `put_and_count` writes the three columns when set. Reads still select the S1 columns (the vector is not read back by `get`).

- [ ] Failing tests: migration 0002 applies on top of 0001 (and on a fresh schema both apply); entries in another scope / partition / embedder are never returned; the nearest of three is returned with the right similarity (vectors chosen so cosines are 1.0, 0.8, 0.1); an expired entry is skipped; dimension mismatch between stored and queried vectors → `StoreError`; `EXPLAIN` of the query on 300 rows shows one index scan / seq scan of the filtered rows (no Python loop — assert one round trip via a counting wrapper is enough).
- [ ] Red → implement → green.

### Task 3: L2 in `CacheHook`, `hit_semantic` counting

**Files:** Modify `src/lite_cache/hook.py`, `src/lite_cache/types.py` (`RoleStats.semantic_hits: int`, `hit_rate` counts both), `src/lite_cache/store.py` (stats `FILTER (WHERE outcome = 'hit_semantic')`), `src/lite_cache/cli.py` (stats line), `src/lite_cache/__init__.py`; Test `tests/contract/test_hook_semantic.py`

**Interfaces:** `CacheHook(store, clock=…, embedder: Embedder | None = None, thresholds: Mapping[str, float] | None = None)` — `thresholds` defaults to `semantic.thresholds()`. The vector from `before` is kept for `after` in a small bounded map keyed by `id(call)` holding the `Call` (identity checked; size ≤ 1024, oldest dropped).

- [ ] Failing tests (real `LiteModel` + recording transport + a fake embedder mapping texts to fixed vectors; threshold 0.9):
  - similar question, same partition → second call sends nothing, `cached`, provenance and usage of the first; stats `semantic_hits == 1`, `hits == 0`.
  - same question, different partition / scope / embedder → miss.
  - similarity below threshold → miss; above but guard says numbers / negation → miss.
  - `semantic=False`, or missing `question` / `partition` → exactly S1 behaviour (no embed call; fake embedder counts calls == 0).
  - `semantic=True` with no embedder / no threshold for its model id → `ConfigError`, no request sent.
  - the embedder is called once per miss (before) and not again in after; the written row has the vector, partition and embedder.
  - embedder raises → `StoreError`-like read failure, no request; wrong dimension → same.
  - an L1 hit never calls the embedder.
- [ ] Red → implement → green; full suite; S3a close-out (`/simplify`, Codex, fix, re-run); propose commit.

---

## S3b — calibration

### Task 4: Datasets

**Files:** Create `eval/pairs/fetch.py` (PAWS en + PAWS-X zh), `eval/pairs/generate.py` (domain set), `eval/pairs/generated.jsonl` (committed), `eval/pairs/review-sample.md`; Modify `pyproject.toml` (`eval` extra: lite-eval 0.4.1 wheel URL, as lite-mem has it).

**Interfaces:** every pair becomes a lite-eval case `{"id", "input": {"q1", "q2"}, "expected": {"same": bool}, "tags": {"set": "paws-en"|"pawsx-zh"|"generated", "split": "dev"|"test", "label": "pos"|"neg", "kind": "adversarial"|"numbers"|"negation"|"entity"|"paraphrase"}}`.
- `fetch.py` downloads PAWS `paws_wiki_labeled_final.tar.gz` and PAWS-X `x-final.tar.gz` into a temp dir, prints sha256, writes the lite-eval datasets there (PAWS dev/test; PAWS-X zh dev_2k/test_2k). PAWS negatives are all `kind: adversarial` (their construction is word swapping), which counts as hard.
- `generate.py` seeds from lite-rag's 70 golden questions (`../lite-rag/eval/golden*.jsonl`), asks local `qwen3:8b` (via lite-model, `reasoning_effort: none`) for 2 paraphrases and 4 hard negatives per seed (a number changed, a negation added, an entity swapped, a scope narrowed), plus the same for ~60 extra questions it writes from lite-rag's corpus documents, so the test split has ≥ 300 negatives; split dev/test by seed (no seed in both); writes `review-sample.md` with a random 10 % for the owner's review.
- [ ] Tests (`tests/unit/test_pairs.py`, no network): the converters on tiny fixture TSVs produce the case shape and tags; no seed appears in both splits of the generated set; the generated file passes the shape check. Seen red first.
- [ ] Run `fetch.py` and `generate.py`; **stop and ask the owner to review `review-sample.md`** (design §8: a human spot check of ≥ 10 % before the generated set counts).

### Task 5: Target, suites and the calibration

**Files:** Create `eval/lite-eval/target.py` (`pairs` target: embeds q1, q2 with the Ollama `bge-m3:567m` through a small eval-only client; outputs `{"cosine": float, "guard": str | null}`), `eval/lite-eval/suites/pairs-{paws-en,pawsx-zh,generated}.yaml`, `eval/calibrate.py`, `kb/eval-results.md`; Modify `src/lite_cache/thresholds.json`.

**Interfaces:** `calibrate.py <run dir>…` reads the outputs; per set: on dev, the smallest threshold `t` (grid 0.80–0.99, step 0.005) whose dev false-hit rate (hit = cosine ≥ t and guard passes) ≤ 0.005, maximising dev hit rate; the embedder's threshold is the **largest** of the sets' `t`; on each test set at that threshold: negatives n, hard share, false hits k, rate k/n, Wilson 95 % upper bound, hit rate on positives; PASS/FAIL per the gate. Writes a dated section to `kb/eval-results.md` (run ids, sha256s, commit) and, only if every set passes, the threshold into `thresholds.json`.
- [ ] Tests (`tests/unit/test_calibrate.py`): Wilson 95 % upper bound (z = 1.96) against hand-computed values — n = 300: k = 0 → 0.01264, k = 1 → 0.01864 (passes ≤ 0.02), k = 2 → 0.02398 (fails); threshold selection on a synthetic score table; the gate fails on 299 negatives, on < half hard, on rate 0.011, on upper 0.021. Seen red first. (So a 300-negative test set tolerates at most one false hit: generate comfortably more than 300 negatives for the test split.)
- [ ] Run the three suites (`uv run --extra eval lite-eval run eval/lite-eval/suites/pairs-*.yaml`, Ollama up), run `calibrate.py`, record the numbers; if a set fails, report — do not loosen the gate (design §8).
- [ ] If the guard leaves a recognisable class of false hits (e.g. entities), propose a guard rule from dev data only, re-run, record before / after.
- [ ] Design status + §12 guard item, S3b close-out (`/simplify`, Codex, fix, re-run), propose commits; release lite-cache 0.2.0 (wheel with `thresholds.json`) as a separate step with the owner's go-ahead.
