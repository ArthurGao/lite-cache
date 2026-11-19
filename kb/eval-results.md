# lite-cache eval results

## 2026-11-20 — L2 calibration, ollama/bge-m3:567m (S3b)

Runs: eval/lite-eval/runs/pairs-public/20261119T101820678769Z-bge-m3-fcc193, eval/lite-eval/runs/pairs-generated/20261119T102053815017Z-bge-m3-0d5984  
Commit: db408c0 (guard applied at calibration)

| set | dev threshold (FHR ≤ 0.005, in [0.80, 0.99]) |
| --- | --- |
| generated | 0.985 |
| paws-en | none |
| pawsx-zh | none |

**No threshold**: none holds the dev margin for paws-en, pawsx-zh.

### Reading (owner decision 2026-11-20: L2 stays off)

No threshold in [0.80, 0.99] holds the dev margin on either PAWS set; the generated set's own dev
threshold (0.985) fails its test gate. A first run of calibrate.py let the threshold go above the
grid: at 1.000 every test set "passed" with 0 / 0.1 / 3.2 % hits — L2 matching only what L1
already matches. Thresholds are now capped at 0.99 (`test_no_threshold_above_the_grid`).
`thresholds.json` stays empty, so `CacheHook(embedder=…)` raises `ConfigError` and L2 cannot be
enabled. Each set at its own dev threshold (uncapped, for the record) and at fixed thresholds, on
test:

| set | t | false hits | Wilson upper | hit rate | gate |
| --- | --- | --- | --- | --- | --- |
| generated | 0.985 (own dev) | 5/334 | 0.0346 | 0.090 | FAIL |
| generated | 0.970 | 16/334 | 0.0764 | 0.306 | FAIL |
| generated | 0.950 | 52/334 | 0.1985 | 0.514 | FAIL |
| paws-en | 0.998 (own dev) | 31/4464 | 0.0098 | 0.050 | PASS |
| paws-en | 0.950 | 3567/4464 | 0.8106 | 0.893 | FAIL |
| pawsx-zh | 1.000 (own dev) | 0/1106 | 0.0035 | 0.032 | PASS |
| pawsx-zh | 0.950 | 373/1106 | 0.3656 | 0.463 | FAIL |

Why: a one-word change that flips the answer (quickly/slowly, 北/南半球, 主任/副主任, 支出/收入, a
word swap in PAWS) keeps cosine at 0.98–0.99 — at or above most true paraphrases. No threshold
separates them; the guard only catches numbers and negation words.

- Guard bug found by the run: a fused "cannot" was not a negation (`\bnot\b`); fixed and tested.
- Tried on dev only, not adopted: a "pure addition" rule (refuse when one question's tokens are all
  in the other and it adds ≥ 2) blocked 57/76 dev negatives above 0.90 (49 scope additions) at a
  cost of 4/108 dev paraphrases; on generated test at its dev threshold (0.979): 5/334, still FAIL
  (hit rate 0.090 → 0.167).
- Generation note: qwen3:8b "changed a number" in 150 of 161 seeds that had none by appending one;
  such negatives are dropped (they are guard-refused by construction and would ease the gate).
  Owner spot check of 10 % (89 pairs): ok. Some label noise remains in `scope` (an added condition
  that does not change the answer), which counts against L2.
- Next, if L2 is wanted: a verifier on the L2 candidate (a cross-encoder, or a local lite-model role
  judging "same answer?"), calibrated on these same sets. Its own stage.

Data: PAWS / PAWS-X from Hugging Face google-research-datasets (sha256 in
`eval/pairs/data/public/sources.json`, not committed); generated set `eval/pairs/generated/`
(committed). Ollama bge-m3:567m, local, free.
