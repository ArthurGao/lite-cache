"""Fetch PAWS (en) and PAWS-X (zh) and write them as one lite-eval dataset (design §8, S3b).

    uv run python eval/pairs/fetch.py

Source: the google-research-datasets org on Hugging Face (the same files as the original
tarballs, which storage.googleapis.com no longer serves). Licence: PAWS / PAWS-X, see
https://github.com/google-research-datasets/paws (the data may be freely used for any purpose).
The data is not committed: it lands in eval/pairs/data/ (gitignored) with each file's sha256.
"""

import json
import sys
import urllib.request
from pathlib import Path

import pyarrow.parquet as pq

from lite_cache.canonical import file_sha256
from lite_cache.pairs import paws_cases

HF = "https://huggingface.co/datasets/google-research-datasets"
# (set, split, url): PAWS validation is our dev, its test our test; the same for PAWS-X zh.
FILES = [
    ("paws-en", "dev", f"{HF}/paws/resolve/main/labeled_final/validation-00000-of-00001.parquet"),
    ("paws-en", "test", f"{HF}/paws/resolve/main/labeled_final/test-00000-of-00001.parquet"),
    ("pawsx-zh", "dev", f"{HF}/paws-x/resolve/main/zh/validation-00000-of-00001.parquet"),
    ("pawsx-zh", "test", f"{HF}/paws-x/resolve/main/zh/test-00000-of-00001.parquet"),
]
DATA = Path(__file__).parent / "data"


def main() -> None:
    raw = DATA / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    out = DATA / "public"
    out.mkdir(parents=True, exist_ok=True)
    cases = []
    sources = []
    for set_name, split, url in FILES:
        path = raw / f"{set_name}-{split}.parquet"
        if not path.exists():
            urllib.request.urlretrieve(url, path)
        digest = file_sha256(path)
        rows = pq.read_table(path).to_pylist()
        part = paws_cases(rows, set_name=set_name, split=split)
        negatives = sum(1 for c in part if not c["expected"]["same"])
        print(f"{set_name} {split}: {len(part)} pairs, {negatives} negatives, sha256 {digest}")
        sources.append({"set": set_name, "split": split, "url": url, "sha256": digest})
        cases.extend(part)
    with (out / "cases.jsonl").open("w") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
    (out / "dataset.yaml").write_text(
        "name: pairs-public\n"
        "version: '2026-11-19'\n"
        "license: other (PAWS / PAWS-X, free to use for any purpose)\n"
        "description: 'L2 calibration (lite-cache design §8): PAWS labeled_final (en)"
        " and PAWS-X zh,"
        " validation as dev and test as test. Negatives are word-swap adversaries (hard).'\n"
    )
    (out / "sources.json").write_text(json.dumps(sources, indent=2) + "\n")
    print(f"wrote {len(cases)} cases to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
