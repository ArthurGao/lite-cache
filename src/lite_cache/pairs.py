"""The L2 pairs evaluation's pure parts (design §8, S3b): turning labelled question pairs into
lite-eval cases, choosing a threshold on dev and gating it on test. No lite-eval import, so it
is unit-tested without the `eval` extra; `eval_target` and `eval/pairs/*.py` use it."""

import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from lite_cache.canonical import sha256_hex
from lite_cache.semantic import numbers

Z95 = 1.96  # two-sided 95 %
MIN_NEGATIVES = 300
MAX_FALSE_HIT_RATE = 0.01
MAX_UPPER_BOUND = 0.02
DEV_MARGIN = 0.005  # the dev false-hit rate a threshold may have: half the gate's
GRID = [round(0.80 + 0.005 * i, 3) for i in range(39)]  # 0.800 … 0.990


def paws_cases(
    rows: Iterable[Mapping[str, Any]], *, set_name: str, split: str
) -> list[dict[str, Any]]:
    """PAWS / PAWS-X rows (`id`, `sentence1`, `sentence2`, `label` 0/1) as lite-eval cases. Their
    negatives are word-swap adversaries, built to look alike: every one counts as hard."""
    cases = []
    for row in rows:
        same = int(row["label"]) == 1
        cases.append(
            {
                "id": f"{set_name}:{split}:{row['id']}",
                "input": {"q1": row["sentence1"], "q2": row["sentence2"]},
                "expected": {"same": same},
                "tags": {
                    "set": set_name,
                    "split": split,
                    "label": "pos" if same else "neg",
                    "kind": "paraphrase" if same else "adversarial",
                },
            }
        )
    return cases


def sample_by_split(
    cases: Sequence[Mapping[str, Any]], *, per_split: int, seed: int
) -> list[Mapping[str, Any]]:
    """Up to `per_split` cases from each (set, split), the same ones for the same seed (design
    §13.3: shorter local runs; every split kept apart)."""
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for case in cases:
        groups.setdefault((case["tags"]["set"], case["tags"]["split"]), []).append(case)
    rng = random.Random(seed)
    picked: list[Mapping[str, Any]] = []
    for key in sorted(groups):
        group = sorted(groups[key], key=lambda c: str(c["id"]))
        picked += group if len(group) <= per_split else rng.sample(group, per_split)
    return picked


def split_by_seed(
    cases: Sequence[Mapping[str, Any]], *, test_share: float
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Generated pairs split so that no seed question (`tags.seed`) has pairs in both: a stable
    hash of the seed decides (the same split on every run)."""
    dev: list[Mapping[str, Any]] = []
    test: list[Mapping[str, Any]] = []
    for case in cases:
        bucket = int(sha256_hex(case["tags"]["seed"])[:8], 16) / 0xFFFFFFFF
        (test if bucket < test_share else dev).append(case)
    return dev, test


@dataclass(frozen=True)
class Scored:
    """One pair as the evaluation saw it: the label, the cosine of the two questions' vectors
    and the guard's verdict (None = it would let them share an answer)."""

    same: bool
    cosine: float
    guard: str | None
    hard: bool
    # The L2 verifier's verdict (S3c, design §13); None when there is none: a run without a
    # verifier, or a pair below the floor grid that no threshold reaches.
    verified: bool | None = None

    @property
    def admitted(self) -> bool:
        """Neither the guard nor the verifier refuses it. A verdict of None passes: only right
        while every threshold is >= GRID[0], the cosine below which the eval target asks none."""
        return self.guard is None and self.verified is not False

    def hit(self, threshold: float) -> bool:
        return self.cosine >= threshold and self.admitted


def wilson_upper(k: int, n: int, z: float = Z95) -> float:
    """The upper end of the Wilson score interval for k successes in n trials."""
    p = k / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre + margin) / (1 + z * z / n)


def select_threshold(
    dev: Sequence[Scored], *, max_false_hit_rate: float = DEV_MARGIN
) -> float | None:
    """The lowest threshold in [GRID[0], GRID[-1]] whose dev false-hit rate is within
    `max_false_hit_rate` (the lowest hits the most positives). Candidates are the grid and the
    negatives' own cosines, so a threshold sits just on a negative rather than between grid
    points. None when none holds: a threshold above the grid hits next to nothing."""
    negatives = [s for s in dev if not s.same]
    if not negatives:
        return None
    allowed = math.floor(max_false_hit_rate * len(negatives))
    # cosines of the negatives the guard and the verifier let through, highest first: at t the false
    # hits are those >= t, so t must sit above the (allowed + 1)-th highest
    passing = sorted((s.cosine for s in negatives if s.admitted), reverse=True)
    floor = passing[allowed] if len(passing) > allowed else -math.inf
    candidates = sorted({*GRID, *(s.cosine for s in negatives)})
    return next((t for t in candidates if GRID[0] <= t <= GRID[-1] and t > floor), None)


@dataclass(frozen=True)
class GateResult:
    negatives: int
    hard: int
    false_hits: int
    rate: float
    upper: float
    positives: int
    hit_rate: float
    reasons: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.reasons


def gate(test: Sequence[Scored], *, threshold: float) -> GateResult:
    """Design §8: at least 300 negatives, at least half of them hard; false-hit rate ≤ 0.01 and
    its 95 % Wilson upper bound ≤ 0.02. Decided on a held-out test set only."""
    negatives = [s for s in test if not s.same]
    positives = [s for s in test if s.same]
    n, hard = len(negatives), sum(s.hard for s in negatives)
    k = sum(s.hit(threshold) for s in negatives)
    rate = k / n if n else 1.0
    upper = wilson_upper(k, n) if n else 1.0
    reasons = []
    if n < MIN_NEGATIVES:
        reasons.append(f"{n} negatives < {MIN_NEGATIVES}")
    if hard * 2 < n:
        reasons.append(f"hard negatives {hard}/{n} < half")
    if rate > MAX_FALSE_HIT_RATE:
        reasons.append(f"false-hit rate {rate:.4f} > {MAX_FALSE_HIT_RATE}")
    if upper > MAX_UPPER_BOUND:
        reasons.append(f"Wilson upper bound {upper:.4f} > {MAX_UPPER_BOUND}")
    hits = sum(s.hit(threshold) for s in positives)
    return GateResult(
        negatives=n,
        hard=hard,
        false_hits=k,
        rate=rate,
        upper=upper,
        positives=len(positives),
        hit_rate=hits / len(positives) if positives else 0.0,
        reasons=reasons,
    )


NEGATIVE_KINDS = ("numbers", "negation", "entity", "scope")
KINDS = ("paraphrase", "adversarial", *NEGATIVE_KINDS)


def variant_cases(
    seed_id: str,
    question: str,
    *,
    paraphrases: Sequence[str],
    negatives: Sequence[tuple[str, str]],
) -> list[dict[str, Any]]:
    """A seed question's generated variants as cases (no split yet; `tags.seed` decides it). A
    variant equal to the seed after trimming is dropped: it is no pair. So is a `numbers`
    negative of a seed without a digit: the number was added, not changed, and the guard refuses
    such a pair by construction (it would make the gate easier)."""
    cases: list[dict[str, Any]] = []
    has_number = bool(numbers(question))
    variants = [("paraphrase", f"p{i}", q) for i, q in enumerate(paraphrases)]
    variants += [
        (kind, f"{kind}{i}", q)
        for i, (kind, q) in enumerate(negatives)
        if kind != "numbers" or has_number
    ]
    for kind, suffix, q in variants:
        if q.strip() == question.strip():
            continue
        same = kind == "paraphrase"
        cases.append(
            {
                "id": f"generated:{seed_id}:{suffix}",
                "input": {"q1": question, "q2": q.strip()},
                "expected": {"same": same},
                "tags": {
                    "set": "generated",
                    "label": "pos" if same else "neg",
                    "kind": kind,
                    "seed": seed_id,
                },
            }
        )
    return cases


def check_case(case: Mapping[str, Any]) -> list[str]:
    """What is wrong with a case's shape (empty: nothing)."""
    problems = []
    q1, q2 = case["input"].get("q1", ""), case["input"].get("q2", "")
    if not q1.strip() or not q2.strip():
        problems.append("empty question")
    tags, same = case["tags"], case["expected"]["same"]
    if tags.get("split") not in ("dev", "test"):
        problems.append(f"split {tags.get('split')!r}")
    if tags.get("kind") not in KINDS:
        problems.append(f"kind {tags.get('kind')!r}")
    if (tags.get("label") == "pos") != same:
        problems.append(f"label {tags.get('label')} but same is {same}")
    return problems


def stored_threshold(threshold: float) -> float:
    """The value written to thresholds.json: rounded *up* to 4 places, as a hit is
    `cosine >= threshold` and a lower value could admit a negative the chosen one excluded."""
    return math.ceil(round(threshold * 10_000, 6)) / 10_000
