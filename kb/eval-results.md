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

## 2026-11-20 — L2 calibration, ollama/bge-m3:567m+ollama:qwen3:8b#0310ecea (S3c)

Runs: eval/lite-eval/runs/pairs-verified-public/20261119T194605435631Z-qwen3-8b-92b533, eval/lite-eval/runs/pairs-verified-generated/20261119T193920454482Z-qwen3-8b-dcc6c4  
Commit: 7e463c5 + the S3c working tree (committed afterwards on feat/s3c-verifier) (guard applied at calibration)

| set | dev threshold (FHR ≤ 0.005, in [0.80, 0.99]) |
| --- | --- |
| generated | 0.975 |
| paws-en | none |
| pawsx-zh | none |

**No threshold**: none holds the dev margin for paws-en, pawsx-zh.

### Reading (S3c, qwen3:8b verifier; owner rule 2026-11-20: FAIL → code merges, L2 stays off)

The verifier helps on our own question pairs but cannot carry PAWS. Generated set at its dev floor
0.975 on test: 2/334 false hits (Wilson upper 0.0216, FAIL by a hair), hit rate 22 % (S3b without a
verifier: 5/334, 9 %). On dev it said "same" for 132 of 133 paraphrases; most negatives it let
through are label noise of the generated set — a "scope" that does not change the answer ("in the
solar system", "垃圾分类中"), near-synonym "entities" (system clock / device's clock, 餐巾纸 / 纸巾),
one broken row — and a few real misses (login / payment page). PAWS / PAWS-X (sampled, seed
20261120) on dev:

| set | floor | false hits (negatives) | hits (positives) |
| --- | --- | --- | --- |
| paws-en | 0.80 | 245/559 (0.44) | 404/441 (0.92) |
| paws-en | 0.99 | 43/559 (0.077) | 176/441 (0.40) |
| pawsx-zh | 0.80 | 229/566 (0.40) | 337/434 (0.78) |
| pawsx-zh | 0.99 | 24/566 (0.042) | 75/434 (0.17) |

PAWS pairs are long declarative sentences with swapped words (Paramaribo / Suriname, Jesus /
Jonah), not questions; qwen3:8b reads them as rewordings. Some PAWS negatives differ only in
punctuation. qwen3:1.7b was not run (the plan: only if 8b passes).

- Guard gap seen on dev: 分别 contains 别, so "分别是哪些" / "分别不是哪些" both "have" a negation and
  pass the guard; a count of negations (1 vs 2) would refuse it. Not adopted in S3c (it would not
  change the outcome); an open guard item (design §12).
- Runs: verified-generated 897 pairs 404 s; verified-public 4000 pairs 2859 s (local, free).

## 2026-11-21 — L2 calibration, ollama/bge-m3:567m+gateway:gpt-sub#370f9979

Runs: eval/lite-eval/runs/pairs-verified-generated-gateway/20261120T181556371152Z-gpt-sub-abb294  
Commit: 7e6520e (guard applied at calibration)

| set | dev threshold (FHR ≤ 0.005, in [0.80, 0.99]) |
| --- | --- |
| generated | 0.975 |

Threshold (largest of the sets): **0.975**

| test set | negatives | hard | false hits | rate | Wilson 95 % upper | positives | hit rate | gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| generated | 334 | 334 | 2 | 0.0060 | 0.0216 | 222 | 0.234 | FAIL: Wilson upper bound 0.0216 > 0.02 |

Test pairs at or above the threshold: the guard refused 14 negatives; the verifier refused 11 negatives and 1 positives.

## 2026-11-21 — L2 calibration, ollama/bge-m3:567m+gateway:claude-sub#370f9979

Runs: eval/lite-eval/runs/pairs-verified-generated-gateway/20261120T180032466540Z-claude-sub-cdad12  
Commit: 7e6520e (guard applied at calibration)

| set | dev threshold (FHR ≤ 0.005, in [0.80, 0.99]) |
| --- | --- |
| generated | 0.975 |

Threshold (largest of the sets): **0.975**

| test set | negatives | hard | false hits | rate | Wilson 95 % upper | positives | hit rate | gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| generated | 334 | 334 | 3 | 0.0090 | 0.0261 | 222 | 0.234 | FAIL: Wilson upper bound 0.0261 > 0.02 |

Test pairs at or above the threshold: the guard refused 14 negatives; the verifier refused 10 negatives and 1 positives.

### 2026-11-21 — what the subscription verifiers show (gpt-sub, claude-sub through llm-gateway)

Both judged the full generated set (844 pairs from cosine 0.80 up, prompt v2 with the injection guard;
qwen3:8b above was prompt v1) through llm-gateway; `judge_backend: gateway` in
`eval/lite-eval/suites/pairs-verified-generated-gateway.yaml`. No errors (one gateway 502, retried).
- All three verifiers land on the same dev floor (0.975) and the same gate result: test false hits
  qwen3:8b 2, gpt-sub 2, claude-sub 3 of 334 (≤ 1 would pass). Above 0.975 only 13 test negatives
  reach the verifier.
- Every false hit, for every model, is a `scope` pair whose answer arguably does not change
  ("杭州办公室楼顶有什么地方可以休息？" vs "…有哪些地方可以供员工休息？"; "酒店最晚什么时候取消还能全额退款？"
  vs "…在预订后最晚…"): the label noise noted in design §13.5. Stronger models agree with each other
  on them, so this set cannot tell verifiers apart; the gate fails on its labels, not on the judge.
- Smoke on both profiles (scratch script, not committed): L1 served the second identical call from
  the cache with one gateway call; `ModelVerifier` 5/5 on the `-m ollama` pairs, injection case included.
- Cost: claude-sub ~1,500 input tokens a call (mostly the Claude CLI's own system prompt), ~4.3 s;
  gpt-sub ~1.2 s p50. PAWS / PAWS-X not run (owner chose the generated set only).

## 2026-11-21 — L2 calibration, ollama/bge-m3:567m+gateway:gpt-sub#370f9979

Runs: eval/lite-eval/runs/pairs-verified-public-gateway/20261120T182800231055Z-gpt-sub-b75f52, eval/lite-eval/runs/pairs-verified-generated-gateway/20261120T181556371152Z-gpt-sub-abb294  
Commit: b033f6e (guard applied at calibration)

| set | dev threshold (FHR ≤ 0.005, in [0.80, 0.99]) |
| --- | --- |
| generated | 0.975 |
| paws-en | none |
| pawsx-zh | none |

**No threshold**: none holds the dev margin for paws-en, pawsx-zh.

## 2026-11-21 — L2 calibration, ollama/bge-m3:567m+gateway:claude-sub#370f9979

Runs: eval/lite-eval/runs/pairs-verified-public-gateway/20261120T195301064422Z-claude-sub-0ef920, eval/lite-eval/runs/pairs-verified-generated-gateway/20261120T180032466540Z-claude-sub-cdad12  
Commit: b033f6e (guard applied at calibration)

| set | dev threshold (FHR ≤ 0.005, in [0.80, 0.99]) |
| --- | --- |
| generated | 0.975 |
| paws-en | none |
| pawsx-zh | none |

**No threshold**: none holds the dev margin for paws-en, pawsx-zh.

### 2026-11-21 — PAWS / PAWS-X with the subscription verifiers (gpt-sub, claude-sub through llm-gateway)

Same sample as qwen3:8b (`eval/pairs/data/public-sample`, 4,000 pairs; ~3,820 judged from cosine 0.80 up per
model), prompt v2, suite `eval/lite-eval/suites/pairs-verified-public-gateway.yaml`. No floor in [0.80, 0.99]
holds dev FHR ≤ 0.005 on either PAWS set for either model: **FAIL, L2 stays off**. False hits among all negatives
of the split, at floor 0.99 (cosine ≥ 0.99 and guard passed and verifier "same"):

| verifier | paws-en dev | paws-en test | pawsx-zh dev | pawsx-zh test | positives kept, en dev |
| --- | --- | --- | --- | --- | --- |
| qwen3:8b (prompt v1, 2026-11-20) | 7.7 % | 6.9 % | 4.2 % | 1.8 % | 176/180 |
| gpt-sub | 2.7 % | 3.3 % | 2.8 % | 1.5 % | 172/180 |
| claude-sub | 2.1 % | 3.3 % | 2.8 % | 1.8 % | 175/180 |

- A stronger judge roughly halves the false hits on English PAWS, little on PAWS-X, and stays 4–6× over the
  0.5 % dev budget. gpt-sub and claude-sub land close together, so the remaining false hits look like the
  task (PAWS negatives are word-swapped statements whose meaning changes subtly), not a weak model.
- Cost: gpt-sub 3,824 calls, 2.2 s mean, 1.33M input tokens; claude-sub 3,825 calls, 5.1 s mean, 5.99M input
  tokens (the Claude CLI's own system prompt), 68 gateway 502s retried, 2 cases re-run after `CallFailed`; no 429.
- What this rules out: "a better verifier model" alone does not open L2 on these gates. The next step stays the
  owner's 2026-11-20 decision: measure how often real users rephrase (llm-steward `retrieval_logs`), then decide.

## 2026-11-22 — how often would L2 fire on real traffic? (read-only, llm-steward dev DB)

`scripts/measure_paraphrase.py` over `steward.retrieval_logs` (a read-only connection), distinct
queries per namespace, ollama/bge-m3:567m, cosine of every pair inside one namespace.

| Sample | Pairs | ≥ 0.90 | ≥ 0.95 | ≥ 0.98 |
| --- | --- | --- | --- | --- |
| 3 namespaces, 18 distinct queries | 76 | 1 | 0 | 0 |

- The one pair at 0.92 is the same question in two languages ("What does the user drink in the
  morning?" / "用户早上喝什么"): same answer. Every other pair is ≤ 0.75 (e.g. "coffee" / "coffee
  refund" 0.74, sibling Kubernetes topics 0.66–0.75): different answers.
- **Not enough to decide anything.** 18 queries from development use, and retrieval queries are only
  a proxy: L2 would see kb.risk prompts (whole entry texts), which repeat as exact copies (L1), not
  paraphrases. What it does show: on this traffic a paraphrase hit is rare, so L2 staying off costs
  almost nothing; re-measure when llm-steward has real users (the script is read-only and re-runnable).
