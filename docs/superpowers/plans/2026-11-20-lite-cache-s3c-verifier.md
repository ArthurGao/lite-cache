# lite-cache S3c — L2 verifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An L2 candidate counts as a hit only when a verifier (a local LLM through a lite-model role) judges that the two questions share an answer; the floor threshold for (embedder, verifier) is calibrated on PAWS / PAWS-X samples and the generated set under the §8 gate.

**Architecture:** A `Verifier` Protocol next to `Embedder`; `CacheHook` calls it after the threshold and the guard; `ModelVerifier` implements it over a host's lite-model role with caching and L2 switched off inside. The pairs evaluation gets a `VerifiedPairsTarget` (cosine + verdict) and a verdict-aware threshold selection.

**Tech Stack:** Python 3.12, lite-cache 0.2.0, lite-model 0.3.0, PostgreSQL + pgvector, lite-eval 0.4.1 (`eval` extra), Ollama `bge-m3:567m` + `qwen3:8b` (then `qwen3:1.7b`).

**Spec:** `docs/lite-cache-design.md` §13 (and §11 S3c row, §0 2026-11-20 rows, §3.4, §3.5, §8).

## Global Constraints

- Hit path order: L1 → embed → `nearest` → similarity ≥ floor → guard → `verifier.same_answer(question, entry.question)`; the verifier is never called for a candidate below the floor or refused by the guard.
- A "different" verdict is a miss; the call proceeds and counts exactly one stats row as before (§3.4); rejections go to `hook.verifier_rejects` (in memory) and a debug log, not the stats table.
- Thresholds with a verifier are keyed `"<embedder model_id>+<verifier model_id>"`; a missing key → `ConfigError` at construction.
- Verifier failure (raise, timeout, off-schema reply) → `VerifierError(StoreError)`: the call fails, nothing is sent.
- `ModelVerifier.model_id = "<type>:<model>#<8 hex>"` — type and model from `model.target(role)`; 8 hex = sha256 of the prompt text plus the role's temperature, `reasoning_effort` and output limit as canonical JSON.
- The verifier's own call runs inside `call_context(cache=None, question=None, partition=None)`: not cached, no L2.
- Calibration: S3b's dev/test split kept; 1000 random pairs per split of paws-en and pawsx-zh (fixed seed, recorded); the generated set in full; verdicts only for cosine ≥ 0.80; floor `t ∈ [0.80, 0.99]`, dev FHR ≤ 0.005, largest over sets; §8 gate on test; thresholds written only with `--write-threshold` after the owner reads the hit rates. qwen3:8b first; qwen3:1.7b only if 8b passes every set.
- If 8b fails: code merges with its tests, no threshold, results in `kb/eval-results.md`; no release in this stage (owner, 2026-11-20).
- AGENTS.md: TDD (every new test seen red), close-out (`/simplify` → Codex → fix → re-run) before the commit; commits with the owner's go-ahead. Ollama only via `brew services run`, stopped after.

## Review Focus

1. **The candidate's stored `question` is None** (an L1 entry has no question; only L2 writes have one, but `nearest` filters on a vector) → the verifier must not be called with None; treat as a miss. Test in Task 1.
2. **The verifier returns a non-bool / the model replies `{"same": "yes"}`** → pydantic rejects it in `ModelVerifier` → `VerifierError`, nothing sent. Test in Task 2.
3. **The verifier runs on a `Bounded(model, 1)` shared with the outer call** → no deadlock (lite-model 0.3.0). Test in Task 2.
4. **A verified pair with cosine < 0.80 in the eval data** → no verdict (`same_answer: None`) and it can never be a hit at any floor. Test in Task 3.
5. **Re-running calibration after only the guard changed** → lite-eval cache reused (the target outputs no guard). Covered by the target's `config()` not containing the guard; asserted in Task 3.

---

### Task 1: Verifier in the hook

**Files:**
- Modify: `src/lite_cache/semantic.py` (`Verifier` Protocol, `threshold_key(embedder, verifier) -> str`), `src/lite_cache/errors.py` (`VerifierError(StoreError)`), `src/lite_cache/hook.py`, `src/lite_cache/__init__.py` (export `Verifier`, `VerifierError`)
- Test: `tests/contract/test_hook_verifier.py`

**Interfaces:**
- Produces: `class Verifier(Protocol): model_id: str; def same_answer(self, question: str, candidate: str) -> bool`; `threshold_key(embedder: Embedder, verifier: Verifier | None) -> str` (`embedder.model_id` without a verifier, `f"{e}+{v}"` with one); `CacheHook(store, clock, *, embedder=None, verifier=None, thresholds=None)`; `CacheHook.verifier_rejects: int`.

- [ ] **Step 1: Write the failing tests** (reuse `FakeEmbedder`, `VECTORS`, `ask`, `Setup` from `tests/contract/test_hook_semantic.py`; a `FakeVerifier(answers: dict[tuple[str,str], bool], fail=False)` records calls; thresholds `{"fake/vec3+fake-judge": 0.99}`):
  - `test_a_same_verdict_is_a_hit`: SAME then PARAPHRASE → 1 upstream request, `hit_semantic` 1, verifier called once with `(PARAPHRASE, SAME)`.
  - `test_a_different_verdict_is_a_miss_counted_in_memory`: verdict False → 2 requests, `hook.verifier_rejects == 1`, stats rows: `stored == 2`, `semantic_hits == 0` (no extra row).
  - `test_the_verifier_is_not_asked_below_the_floor_or_after_the_guard`: FAR and OTHER_NUMBER_2 cases → `verifier.calls == []`.
  - `test_a_verifier_failure_fails_the_call_unsent`: `fail=True` → `VerifierError`, `issubclass(VerifierError, StoreError)`, `up.requests == []`.
  - `test_thresholds_are_keyed_by_embedder_and_verifier`: `CacheHook(..., embedder=FakeEmbedder(), verifier=FakeVerifier(), thresholds={"fake/vec3": 0.99})` → `ConfigError` matching `"fake/vec3+fake-judge"`.
  - `test_an_entry_without_a_question_is_a_miss` (Review Focus 1): put an L2 entry with `question=None` directly via the store → no verifier call, miss.
- [ ] **Step 2: Run** `uv run --env-file .env pytest -q tests/contract/test_hook_verifier.py` — Expected: errors / fails (no `Verifier`, `VerifierError`).
- [ ] **Step 3: Implement** in `hook.py`: construction looks up `threshold_key(embedder, verifier)`; in `_similar`, after the guard: `entry.question is None` → miss; `_verify(question, entry.question)` wraps any exception as `VerifierError(f"verifier {model_id}: {type(exc).__name__}")`; False → `verifier_rejects += 1`, `log.debug`, return None.
- [ ] **Step 4: Run** the file and the whole suite — Expected: all pass.

### Task 2: `ModelVerifier`

**Files:**
- Create: `src/lite_cache/verifier.py`, `src/lite_cache/verifier_prompt_v1.txt`
- Modify: `pyproject.toml` (`[tool.pytest.ini_options]`: marker `ollama: real local Ollama (opt-in)`, `addopts = "-m 'not ollama'"`), `src/lite_cache/__init__.py` (export `ModelVerifier`)
- Test: `tests/contract/test_model_verifier.py`, `tests/ollama/test_model_verifier_live.py`

**Interfaces:**
- Consumes: Task 1's `Verifier`, `VerifierError`.
- Produces: `ModelVerifier(model: lite_model.Model, role: str, *, target: Target | None = None, prompt_version: str = "v1")` — `target` defaults to `model.target(role)` (a `Bounded` has no `target()`: pass it); `.model_id`; `.same_answer(question, candidate) -> bool`; `class SameAnswer(BaseModel): same: bool` (strict bool).

- [ ] **Step 1: Write the failing tests** (a real `LiteModel` on `tests.upstream.Upstream`; roles `answer` + `cache_verifier`; `local_model` gets a `cache_verifier` role):
  - `test_the_verdict_comes_from_the_role`: upstream replies `{"same": true}` → `True`; the request names the role's model and carries the strict `SameAnswer` schema; both questions appear in the user message.
  - `test_the_verifier_call_is_neither_cached_nor_l2`: register a recording hook; inside `call_context(scope="acme", cache=Policy(semantic=True), question="q", partition="p")` call `same_answer` → the hook saw `call.context["cache"] is None`, `question is None`, `partition is None`.
  - `test_an_off_schema_reply_is_a_verifier_error` (Review Focus 2): `{"same": "yes"}` → `VerifierError`.
  - `test_the_model_id_changes_with_the_prompt_and_params`: same role, `prompt_version="v1"` vs a monkeypatched prompt text → different ids; temperature 0 vs 0.5 → different ids; format `ollama:qwen3:8b#[0-9a-f]{8}`.
  - `test_no_deadlock_through_a_shared_bounded` (Review Focus 3): `bounded = Bounded(model, 1)`; a `CacheHook` with `verifier=ModelVerifier(bounded, "cache_verifier", target=model.target("cache_verifier"))` on `model`; an L2 paraphrase through `bounded.text(...)` returns within 3 s (thread + join timeout, as lite-model's `test_bounded_hooks.py`).
  - `tests/ollama/…` (`@pytest.mark.ollama`): four pairs (paraphrase → True; number changed, antonym quickly/slowly, entity 北/南半球 → False) on qwen3:8b with `reasoning_effort: none`, temperature 0.
- [ ] **Step 2: Run** the contract file — Expected: fail (no module).
- [ ] **Step 3: Implement** `verifier.py`: prompt read via `importlib.resources`; system = prompt text, user = `"A: {question}\nB: {candidate}"`; call `model.structured(role, messages, SameAnswer)` inside the `call_context` above; any exception → `VerifierError`. Prompt v1 text per design §13.2 (English, symmetric, any difference in number / negation / object / scope / time → not the same; judge the questions only).
- [ ] **Step 4: Run** contract tests + full suite; then `-m ollama` with Ollama via `brew services run` (stop after) — Expected: pass; record the four live verdicts in the ledger.

### Task 3: Verified pairs evaluation

**Files:**
- Modify: `src/lite_cache/pairs.py` (`Scored.verified: bool | None = None`; `hit(t) = cosine >= t and guard is None and verified is not False` — None means no verdict: an S3b run, or a pair below 0.80 that no floor reaches; `sample_by_split(cases, per_split, seed) -> list`), `src/lite_cache/eval_target.py` (`VerifiedPairsTarget`), `eval/pairs/calibrate.py` (key = `"<embedder>+<verifier>"` when runs carry a verifier; reports verifier rejects of negatives and of positives per test set)
- Create: `eval/pairs/sample.py` (writes `eval/pairs/data/public-sample/` from `data/public/`, gitignored), `eval/lite-eval/suites/pairs-verified-{public,generated}.yaml`
- Test: `tests/unit/test_pairs.py`, `tests/unit/test_eval_target.py`

**Interfaces:**
- Consumes: Task 2's `ModelVerifier`.
- Produces: `VerifiedPairsTarget(model="bge-m3:567m", dim=1024, judge="qwen3:8b")` — `config() == {"embedder": ..., "verifier": verifier.model_id}`, output `{"cosine": float, "same_answer": bool | None}` (None below 0.80); entry point `lite_cache.eval_target:verified_pairs`.

- [ ] **Step 1: Write the failing tests**:
  - `test_a_verified_hit_needs_all_three`: `Scored(same=False, cosine=0.99, guard=None, hard=True, verified=False).hit(0.9) is False`; `verified=True` → True; `verified=None` (unverified run) keeps S3b behaviour.
  - `test_select_threshold_with_verdicts`: negatives at 0.95 judged False do not count → a lower floor is chosen than without verdicts.
  - `test_sampling_keeps_splits_and_is_deterministic`: 1000 per (set, split), same seed → same ids, no case outside its split.
  - `test_below_the_floor_no_verdict` (Review Focus 4): stub embedder + stub Ollama chat endpoint; a pair at cosine 0.5 → `same_answer is None`, the chat endpoint not called.
  - `test_the_config_names_embedder_and_verifier_not_the_guard` (Review Focus 5).
- [ ] **Step 2: Run** — Expected: fail.
- [ ] **Step 3: Implement** as in Interfaces; `VerifiedPairsTarget` builds the verifier with `lite_model.build(ModelsConfig(backend="ollama", model=judge, reasoning_effort="none"), RoleSpec({"cache_verifier": RolePolicy()}))` — the same code path as a host.
- [ ] **Step 4: Run** unit tests + full suite — Expected: pass.

### Task 4: Calibrate, record, close out

**Files:**
- Modify: `kb/eval-results.md`, `docs/lite-cache-design.md` (status, §13 result), `README.md` (Verifier, ModelVerifier), possibly `src/lite_cache/thresholds.json` (only with the owner's go-ahead)

- [ ] **Step 1:** `uv run python eval/pairs/sample.py` (seed 20261120); record the sampled counts and negatives per split.
- [ ] **Step 2:** Ollama via `brew services run`; `uv run lite-eval run eval/lite-eval/suites/pairs-verified-public.yaml` and `…-generated.yaml` (judge qwen3:8b); stop Ollama.
- [ ] **Step 3:** `uv run python eval/pairs/calibrate.py <runs>` — Expected: a report per set (floor, false hits, Wilson upper, hit rate, verifier rejects). If every set passes: repeat Steps 2–3 with `judge: qwen3:1.7b` and stop for the owner to choose and to approve `--write-threshold`. If any fails: record with `--write` only.
- [ ] **Step 4:** Docs (design status + §13 result, README), stage close-out (`/simplify` → Codex → fix → re-run all checks), propose the commit.
