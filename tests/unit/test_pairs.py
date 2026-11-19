"""The pairs evaluation's pure parts (design §8, S3b): dataset conversion, the Wilson bound,
threshold selection on dev and the gate on test."""

from typing import Any

import pytest

from lite_cache.pairs import Scored, gate, paws_cases, select_threshold, split_by_seed, wilson_upper


def test_paws_rows_become_lite_eval_cases() -> None:
    rows = [
        {"id": 7, "sentence1": "A b c.", "sentence2": "C b a.", "label": 0},
        {"id": 8, "sentence1": "X y.", "sentence2": "X, y.", "label": 1},
    ]
    neg, pos = paws_cases(rows, set_name="paws-en", split="test")
    assert neg == {
        "id": "paws-en:test:7",
        "input": {"q1": "A b c.", "q2": "C b a."},
        "expected": {"same": False},
        "tags": {"set": "paws-en", "split": "test", "label": "neg", "kind": "adversarial"},
    }
    assert pos["expected"] == {"same": True}
    assert pos["tags"]["label"] == "pos" and pos["tags"]["kind"] == "paraphrase"


@pytest.mark.parametrize(
    ("k", "n", "upper"),
    [(0, 300, 0.01264), (1, 300, 0.01864), (2, 300, 0.02398), (11, 1000, 0.01959)],
)
def test_wilson_upper_bound(k: int, n: int, upper: float) -> None:
    assert wilson_upper(k, n) == pytest.approx(upper, abs=5e-5)


def _neg(cosine: float, guard: str | None = None, hard: bool = True) -> Scored:
    return Scored(same=False, cosine=cosine, guard=guard, hard=hard)


def _pos(cosine: float) -> Scored:
    return Scored(same=True, cosine=cosine, guard=None, hard=False)


def test_the_threshold_is_the_lowest_whose_dev_false_hit_rate_is_within_the_margin() -> None:
    # 200 negatives: one at 0.95, the rest far; a false-hit rate <= 0.005 allows that one hit.
    dev = [_neg(0.95), _neg(0.951), *[_neg(0.5)] * 198, _pos(0.97), _pos(0.93)]
    t = select_threshold(dev, max_false_hit_rate=0.005)
    assert t == pytest.approx(0.951)  # 0.95 lets both through (1 %); 0.951 lets one (0.5 %)
    # a negative the guard refuses never counts as a hit, whatever its cosine
    guarded = [_neg(0.99, guard="numbers"), *[_neg(0.5)] * 199, _pos(0.9)]
    assert select_threshold(guarded, max_false_hit_rate=0.005) == pytest.approx(0.8)


def test_no_threshold_when_none_holds_the_margin() -> None:
    dev = [_neg(0.999)] * 10 + [_neg(0.5)] * 90
    assert select_threshold(dev, max_false_hit_rate=0.005) is None


def test_no_threshold_above_the_grid() -> None:
    # A threshold above 0.99 hits next to nothing (2026-11-20: 1.000 "passed" with 0-3 % hits):
    # it is reported as no threshold, not as a pass.
    dev = [_neg(0.995)] * 3 + [_neg(0.997)] + [_neg(0.5)] * 196 + [_pos(0.999)]  # 0.997: 1 in 200
    assert select_threshold(dev, max_false_hit_rate=0.005) is None


def _test_set(negatives: int, false_hits: int, hard: int | None = None) -> list[Scored]:
    hard = negatives if hard is None else hard
    rows = [_neg(0.99 if i < false_hits else 0.1, hard=i < hard) for i in range(negatives)]
    return [*rows, *[_pos(0.99)] * 50]


def test_the_gate() -> None:
    passing = gate(_test_set(400, 1), threshold=0.9)
    assert passing.passed and passing.false_hits == 1 and passing.negatives == 400
    assert passing.hit_rate == 1.0
    assert not gate(_test_set(299, 0), threshold=0.9).passed  # too few negatives
    assert not gate(_test_set(400, 0, hard=199), threshold=0.9).passed  # under half hard
    assert not gate(_test_set(1000, 11), threshold=0.9).passed  # rate 0.011
    over = gate(_test_set(300, 2), threshold=0.9)  # rate 0.0067 but upper 0.024
    assert not over.passed and "upper" in " ".join(over.reasons)


def test_generated_pairs_split_by_seed() -> None:
    cases = [{"id": f"s{i % 7}:{i}", "tags": {"seed": f"s{i % 7}"}} for i in range(70)]
    dev, test = split_by_seed(cases, test_share=0.5)
    assert {c["tags"]["seed"] for c in dev}.isdisjoint({c["tags"]["seed"] for c in test})
    assert len(dev) + len(test) == 70
    assert split_by_seed(cases, test_share=0.5) == (dev, test)  # deterministic


def test_a_seed_and_its_variants_become_cases() -> None:
    from lite_cache.pairs import variant_cases

    cases = variant_cases(
        "x01",
        "What does error E4012 mean?",
        paraphrases=["What is the meaning of error E4012?"],
        negatives=[
            ("numbers", "What does error E4013 mean?"),
            ("entity", "What does warning E4012 mean?"),
        ],
    )
    assert [c["id"] for c in cases] == [
        "generated:x01:p0",
        "generated:x01:numbers0",
        "generated:x01:entity1",
    ]
    assert cases[0]["input"] == {
        "q1": "What does error E4012 mean?",
        "q2": "What is the meaning of error E4012?",
    }
    assert [c["expected"]["same"] for c in cases] == [True, False, False]
    assert cases[1]["tags"] == {
        "set": "generated",
        "label": "neg",
        "kind": "numbers",
        "seed": "x01",
    }
    # a variant equal to its seed (after trimming) is no pair at all
    assert variant_cases("s", "Same?", paraphrases=[" Same? "], negatives=[]) == []


def test_check_case() -> None:
    from lite_cache.pairs import check_case

    good: dict[str, Any] = {
        "id": "generated:x01:p0",
        "seed": "x01",
        "input": {"q1": "a?", "q2": "b?"},
        "expected": {"same": True},
        "tags": {"set": "generated", "split": "test", "label": "pos", "kind": "paraphrase"},
    }
    assert check_case(good) == []
    assert check_case({**good, "expected": {"same": False}}) == ["label pos but same is False"]
    bad_kind = {
        **good,
        "tags": {**good["tags"], "label": "neg", "kind": "vibes"},
        "expected": {"same": False},
    }
    assert check_case(bad_kind) == ["kind 'vibes'"]
    assert check_case({**good, "input": {"q1": "a?", "q2": ""}}) == ["empty question"]
    assert check_case({**good, "tags": {**good["tags"], "split": "train"}}) == ["split 'train'"]


def test_a_numbers_negative_needs_a_number_in_the_seed() -> None:
    # qwen "changes a number" in a question that has none by appending one ("… (2025)"): the guard
    # refuses such a pair by construction, so it would make the gate easier, not harder.
    from lite_cache.pairs import variant_cases

    no_digit = variant_cases(
        "s",
        "Which office has a library?",
        paraphrases=[],
        negatives=[
            ("numbers", "Which office has a library? (2025)"),
            ("entity", "Which shop has a library?"),
        ],
    )
    assert [c["tags"]["kind"] for c in no_digit] == ["entity"]
    with_digit = variant_cases(
        "s", "Refunds over 1000?", paraphrases=[], negatives=[("numbers", "Refunds over 2000?")]
    )
    assert [c["tags"]["kind"] for c in with_digit] == ["numbers"]


def test_the_committed_generated_set_is_well_formed() -> None:
    import json
    from pathlib import Path

    from lite_cache.pairs import MIN_NEGATIVES, check_case

    path = Path(__file__).parents[2] / "eval/pairs/generated/cases.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(r["id"], check_case(r)) for r in rows if check_case(r)] == []
    splits = {
        s: {r["tags"]["seed"] for r in rows if r["tags"]["split"] == s} for s in ("dev", "test")
    }
    assert splits["dev"].isdisjoint(splits["test"])
    test_negatives = sum(r["tags"]["split"] == "test" and not r["expected"]["same"] for r in rows)
    assert test_negatives >= MIN_NEGATIVES


@pytest.mark.parametrize("chosen", [0.97931, 0.97938, 0.9794, 0.98])
def test_a_stored_threshold_never_lies_below_the_chosen_one(chosen: float) -> None:
    # Codex S3b: hits are `cosine >= threshold`; rounding 0.97931 down to 0.9793 would admit a
    # negative the chosen threshold excluded. Round up (stricter), to 4 places.
    from lite_cache.pairs import stored_threshold

    stored = stored_threshold(chosen)
    assert chosen <= stored <= chosen + 1e-4
    assert stored == round(stored, 4)
