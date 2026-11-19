"""Generate the domain pairs set for L2 calibration (design §8, S3b) with local qwen3:8b.

    uv run python eval/pairs/generate.py      # needs Ollama with qwen3:8b on 127.0.0.1:11434

Seeds: lite-rag's 70 golden questions, plus questions qwen writes from lite-rag's eval corpus
documents (so the test split has at least 300 negatives). For each seed: 2 paraphrases (same
answer) and up to 4 hard negatives (a number changed, a negation added, an entity swapped, the
scope changed: a different answer). Split dev/test by seed (no seed in both). Writes the lite-eval
dataset eval/pairs/generated/ (committed) and review-sample.md, a random 10 % for the owner's
spot check, which must pass before the set counts (design §8).
"""

import json
import random
import sys
from pathlib import Path
from typing import Any

from lite_model import Message, ModelsConfig, RolePolicy, RoleSpec, build
from pydantic import BaseModel, Field

from lite_cache.pairs import NEGATIVE_KINDS, check_case, split_by_seed, variant_cases

HERE = Path(__file__).parent
LITE_RAG = HERE.parents[2] / "lite-rag" / "eval"
CORPORA = ["corpus", "corpus-exact", "corpus-multihop"]
QUESTIONS_PER_DOC = 6
TEST_SHARE = 0.6
SAMPLE_SHARE = 0.10

MODEL = build(
    ModelsConfig(backend="ollama", model="qwen3:8b", reasoning_effort="none"),
    RoleSpec({"generate": RolePolicy()}),
)


class Questions(BaseModel):
    questions: list[str] = Field(min_length=1)


class Variants(BaseModel):
    paraphrases: list[str] = Field(min_length=2, max_length=2)
    numbers: str | None = None
    negation: str
    entity: str
    scope: str


ASK_QUESTIONS = """Write {n} different questions a user could ask that this document answers.
Write them in the document's language. Each is one short, natural question; include specific
details (names, numbers, codes, conditions) when the document has them.

Document:
{doc}"""

ASK_VARIANTS = """You build test pairs for a semantic cache: a cached answer to question A may be
reused for question B only if B has exactly the same answer.

Question A: {q}

Write, in the same language as A:
- "paraphrases": 2 rewordings of A that ask exactly the same thing (same answer), using different
  words or word order.
- "numbers": A with one number, code, date or amount changed so the answer differs; null if A has
  no number, code, date or amount.
- "negation": A turned into its negative or opposite (e.g. "can" -> "cannot"), so the
  answer differs.
- "entity": A about a different but similar thing (another product, person, place, item or
  category), otherwise worded as closely to A as possible, so the answer differs.
- "scope": A narrowed or changed in condition or scope (e.g. "for international trips", "in
  winter", "for admins"), otherwise worded as closely to A as possible, so the answer differs.
Each negative must stay close to A in wording; only the one change makes the answer differ."""


def golden() -> list[dict[str, str]]:
    seeds = []
    for path in sorted(LITE_RAG.glob("golden*.jsonl")):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            seeds.append({"id": f"golden-{row['id']}", "question": row["question"]})
    return seeds


def from_documents() -> list[dict[str, str]]:
    seeds = []
    for corpus in CORPORA:
        for doc in sorted((LITE_RAG / corpus).glob("*.md")):
            prompt = ASK_QUESTIONS.format(n=QUESTIONS_PER_DOC, doc=doc.read_text()[:6000])
            asked = MODEL.structured("generate", [Message("user", prompt)], Questions).value
            for i, q in enumerate(asked.questions[:QUESTIONS_PER_DOC]):
                seeds.append({"id": f"doc-{corpus}-{doc.stem}-{i}", "question": q.strip()})
            print(f"  {corpus}/{doc.name}: {len(asked.questions)}", file=sys.stderr)
    return seeds


def variants(seed: dict[str, str]) -> list[dict[str, Any]]:
    prompt = ASK_VARIANTS.format(q=seed["question"])
    v = MODEL.structured("generate", [Message("user", prompt)], Variants).value
    negatives = [(k, getattr(v, k)) for k in NEGATIVE_KINDS]
    return variant_cases(
        seed["id"],
        seed["question"],
        paraphrases=v.paraphrases,
        negatives=[(k, q) for k, q in negatives if q],
    )


def main() -> None:
    seeds = golden()
    print(f"golden seeds: {len(seeds)}; writing questions from documents …", file=sys.stderr)
    seeds += from_documents()
    cases: list[dict[str, Any]] = []
    failed = 0
    for n, seed in enumerate(seeds, 1):
        try:
            cases += variants(seed)
        except Exception as exc:  # a malformed reply drops this seed, counted below
            failed += 1
            print(f"  skip {seed['id']}: {type(exc).__name__}", file=sys.stderr)
        if n % 20 == 0:
            print(f"  {n}/{len(seeds)} seeds", file=sys.stderr)
    dev, test = split_by_seed(cases, test_share=TEST_SHARE)
    out = HERE / "generated"
    out.mkdir(exist_ok=True)
    written = []
    for split, part in (("dev", dev), ("test", test)):
        for case in part:
            row = {**case, "tags": {**case["tags"], "split": split}}
            problems = check_case(row)
            if problems:
                raise SystemExit(f"{row['id']}: {problems}")
            written.append(row)
    with (out / "cases.jsonl").open("w") as f:
        for row in written:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out / "dataset.yaml").write_text(
        "name: pairs-generated\n"
        "version: '2026-11-19'\n"
        "synthetic: true\n"
        "license: Apache-2.0\n"
        "description: 'L2 calibration (lite-cache design §8): question pairs generated by qwen3:8b"
        " from lite-rag golden questions and eval corpus; paraphrases and hard negatives (numbers,"
        " negation, entity, scope), split by seed. Spot-checked by the owner (review-sample.md).'\n"
    )
    sample = random.Random(20261119).sample(written, max(1, round(len(written) * SAMPLE_SHARE)))
    lines = [
        "# Generated pairs — review sample",
        "",
        f"{len(sample)} of {len(written)} pairs (10 %, random seed 20261119). For each row: is the",
        "label right? `same` = B has exactly the same answer as A. Mark a wrong row with ✗.",
        "",
        "| # | kind | same | A | B | ok? |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for i, row in enumerate(sorted(sample, key=lambda r: r["id"]), 1):
        a, b = (row["input"][k].replace("|", "\\|") for k in ("q1", "q2"))
        same = "yes" if row["expected"]["same"] else "no"
        lines.append(f"| {i} | {row['tags']['kind']} | {same} | {a} | {b} | |")
    (HERE / "review-sample.md").write_text("\n".join(lines) + "\n")
    stats = {
        split: {
            "pairs": sum(r["tags"]["split"] == split for r in written),
            "negatives": sum(
                r["tags"]["split"] == split and not r["expected"]["same"] for r in written
            ),
        }
        for split in ("dev", "test")
    }
    print(json.dumps({"seeds": len(seeds), "failed_seeds": failed, **stats}))


if __name__ == "__main__":
    main()
