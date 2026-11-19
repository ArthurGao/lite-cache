"""Sample the public pairs for the S3c verifier evaluation (design §13.3).

    uv run python eval/pairs/sample.py      # after eval/pairs/fetch.py

Up to 1000 pairs from each (set, split) of eval/pairs/data/public, seed 20261120, written as the
lite-eval dataset eval/pairs/data/public-sample/ (gitignored, like the source)."""

import json
from collections import Counter
from pathlib import Path

from lite_cache.pairs import sample_by_split

DATA = Path(__file__).parent / "data"
SEED = 20261120
PER_SPLIT = 1000


def main() -> None:
    source = DATA / "public" / "cases.jsonl"
    cases = [json.loads(line) for line in source.read_text().splitlines()]
    picked = sample_by_split(cases, per_split=PER_SPLIT, seed=SEED)
    out = DATA / "public-sample"
    out.mkdir(exist_ok=True)
    with (out / "cases.jsonl").open("w") as f:
        for case in picked:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
    (out / "dataset.yaml").write_text(
        "name: pairs-public-sample\n"
        "version: '2026-11-20'\n"
        "license: other (PAWS / PAWS-X, free to use for any purpose)\n"
        f"description: 'S3c (lite-cache design §13.3): up to {PER_SPLIT} pairs per set and split"
        f" of pairs-public, seed {SEED}.'\n"
    )
    counts = Counter((c["tags"]["set"], c["tags"]["split"], c["tags"]["label"]) for c in picked)
    for key in sorted(counts):
        print(*key, counts[key])


if __name__ == "__main__":
    main()
