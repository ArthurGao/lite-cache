"""Choose the L2 threshold on dev and gate it on test (design §8, S3b).

    uv run python eval/pairs/calibrate.py RUN_DIR... [--write [--write-threshold]]

RUN_DIRs are lite-eval runs of eval/lite-eval/suites/pairs-*.yaml, all of one embedder. The
runs hold only cosines; the current guard is applied here, so a guard change needs no re-run.
Per set (paws-en, pawsx-zh, generated): on dev, the lowest threshold in [0.80, 0.99] whose
false-hit rate is ≤ 0.005; the embedder's threshold is the largest of them. Each test set is then
gated at that threshold (≥ 300 negatives, ≥ half hard, false-hit rate ≤ 0.01, Wilson 95 % upper
bound ≤ 0.02). Prints a Markdown report; a failing set is reported, never "fixed" by loosening
the gate.

--write appends the report to kb/eval-results.md. The threshold reaches
src/lite_cache/thresholds.json only with --write-threshold as well, on a PASS: the owner's call
after reading the hit rates.
"""

import argparse
import json
import subprocess
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import NamedTuple

from lite_eval.datasets.local import read_cases
from lite_eval.store import load_run

from lite_cache.pairs import Scored, gate, select_threshold, stored_threshold
from lite_cache.semantic import guard

REPO = Path(__file__).parents[2]
DATASETS = [REPO / "eval/pairs/data/public", REPO / "eval/pairs/generated"]

Scores = dict[tuple[str, str], list[Scored]]


class Pair(NamedTuple):
    q1: str
    q2: str
    same: bool
    set: str
    split: str


def _pairs() -> dict[str, Pair]:
    pairs: dict[str, Pair] = {}
    for root in DATASETS:
        if not (root / "cases.jsonl").exists():
            continue
        for case_id, case in read_cases(root).items():
            if case_id in pairs:
                raise SystemExit(f"case {case_id} is in more than one dataset")
            assert isinstance(case.input, dict) and isinstance(case.expected, dict)
            pairs[case_id] = Pair(
                str(case.input["q1"]),
                str(case.input["q2"]),
                bool(case.expected["same"]),
                case.tags["set"],
                case.tags["split"],
            )
    return pairs


def _scored(run_dirs: list[Path]) -> tuple[Scores, str]:
    """(set, split) -> scored pairs, and the one embedder `model_id` the runs measured."""
    pairs = _pairs()
    by_set: Scores = defaultdict(list)
    embedders: set[str] = set()
    for directory in run_dirs:
        run = load_run(directory)
        embedders.add(str(run.record.target.config["embedder"]))
        for out in run.outputs:
            if out.error is not None or not isinstance(out.value, dict):
                raise SystemExit(f"{out.case_id}: no output ({out.error}); re-run the suite")
            p, cosine = pairs[out.case_id], out.value["cosine"]
            assert isinstance(cosine, float)
            by_set[(p.set, p.split)].append(
                Scored(
                    same=p.same,
                    cosine=cosine,
                    guard=guard(p.q1, p.q2),
                    hard=not p.same,  # PAWS: word-swap adversaries; generated: one change each
                )
            )
    if len(embedders) != 1:
        raise SystemExit(f"the runs must measure one embedder, not {sorted(embedders)}")
    return by_set, embedders.pop()


def report(run_dirs: list[Path]) -> tuple[str, str, float | None, bool]:
    """(Markdown, embedder, threshold, every test set passed)."""
    by_set, embedder = _scored(run_dirs)
    sets = sorted({s for s, _ in by_set})
    commit = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    lines = [
        f"## {date.today()} — L2 calibration, {embedder} (S3b)",
        "",
        f"Runs: {', '.join(str(r.relative_to(REPO)) for r in run_dirs)}  ",
        f"Commit: {commit} (guard applied at calibration)",
        "",
        "| set | dev threshold (FHR ≤ 0.005, in [0.80, 0.99]) |",
        "| --- | --- |",
    ]
    dev = {s: select_threshold(by_set.get((s, "dev"), [])) for s in sets}
    lines += [f"| {s} | {'none' if t is None else f'{t:.3f}'} |" for s, t in dev.items()]
    missing = [s for s, t in dev.items() if t is None]
    if missing:
        lines += ["", f"**No threshold**: none holds the dev margin for {', '.join(missing)}."]
        return "\n".join(lines) + "\n", embedder, None, False
    threshold = max(t for t in dev.values() if t is not None)
    lines += [
        "",
        f"Threshold (largest of the sets): **{threshold:.3f}**",
        "",
        "| test set | negatives | hard | false hits | rate | Wilson 95 % upper | positives"
        " | hit rate | gate |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    passed = True
    for s in sets:
        r = gate(by_set.get((s, "test"), []), threshold=threshold)
        passed &= r.passed
        verdict = "PASS" if r.passed else "FAIL: " + "; ".join(r.reasons)
        lines.append(
            f"| {s} | {r.negatives} | {r.hard} | {r.false_hits} | {r.rate:.4f} | {r.upper:.4f}"
            f" | {r.positives} | {r.hit_rate:.3f} | {verdict} |"
        )
    refused = sum(
        1
        for (_, split), rows in by_set.items()
        if split == "test"
        for x in rows
        if not x.same and x.cosine >= threshold and x.guard is not None
    )
    lines += ["", f"Test negatives at or above the threshold that the guard refused: {refused}."]
    return "\n".join(lines) + "\n", embedder, threshold, passed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--write", action="store_true", help="append to kb/eval-results.md")
    parser.add_argument(
        "--write-threshold",
        action="store_true",
        help="with --write, also write the threshold into thresholds.json on a PASS",
    )
    args = parser.parse_args()
    text, embedder, threshold, passed = report([r.resolve() for r in args.runs])
    print(text)
    written = False
    if args.write:
        kb = REPO / "kb/eval-results.md"
        existing = kb.read_text() if kb.exists() else "# lite-cache eval results\n\n"
        kb.write_text(existing.rstrip("\n") + "\n\n" + text)
        if passed and threshold is not None and args.write_threshold:
            path = REPO / "src/lite_cache/thresholds.json"
            table = json.loads(path.read_text())
            table[embedder] = stored_threshold(threshold)
            path.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n")
            written = True
    verdict = "PASS" if passed else "FAIL"
    print(f"{verdict}: {'threshold written' if written else 'thresholds.json unchanged'}")


if __name__ == "__main__":
    main()
