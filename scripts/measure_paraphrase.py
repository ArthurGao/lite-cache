"""How often would L2 (semantic matching) fire on real traffic? A read-only measurement on
llm-steward's retrieval log (design §8; architecture review 2026-11-21).

It reads the distinct `query` texts per namespace from `<schema>.retrieval_logs` over a connection
that refuses writes (`default_transaction_read_only`), embeds them with bge-m3 on the local Ollama
(through lite-model's embed role), and counts the pairs inside one namespace whose cosine is at or
above each threshold. It prints JSON: the counts, and the closest pairs for a person to judge
(same answer or not). It writes nothing anywhere.

A proxy, not the real thing: these are retrieval queries, not the kb.risk / kb.worthiness prompts
the cache would see. Report the sample size with every number.

    uv run python scripts/measure_paraphrase.py "postgresql://user:pass@localhost:5432/llm_steward"
"""

import argparse
import itertools
import json
import math
import sys
from collections import defaultdict

import psycopg
from lite_model import ModelsConfig, RolePolicy, RoleSpec, build
from psycopg import sql

THRESHOLDS = (0.90, 0.95, 0.98)
SAMPLE = 30


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def queries(url: str, schema: str) -> dict[str, list[str]]:
    with psycopg.connect(url, options="-c default_transaction_read_only=on") as conn:
        rows = conn.execute(
            sql.SQL("SELECT DISTINCT namespace_id::text, query FROM {} ORDER BY 1, 2").format(
                sql.Identifier(schema, "retrieval_logs")
            )
        ).fetchall()
    by_ns: dict[str, list[str]] = defaultdict(list)
    for ns, query in rows:
        if query and query.strip():
            by_ns[ns].append(query.strip())
    return by_ns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("database_url", help="libpq URL of llm-steward's database (read only)")
    parser.add_argument("--schema", default="steward")
    parser.add_argument("--model", default="bge-m3:567m")
    args = parser.parse_args()

    by_ns = queries(args.database_url, args.schema)
    model = build(
        ModelsConfig(backend="ollama", model=args.model),
        RoleSpec({"embed": RolePolicy(kind="embed")}),
    )
    pairs: list[tuple[float, str, str, str]] = []
    for ns, texts in by_ns.items():
        if len(texts) < 2:
            continue
        vectors = model.embed("embed", texts).vectors
        for (a, va), (b, vb) in itertools.combinations(zip(texts, vectors, strict=True), 2):
            pairs.append((cosine(va, vb), ns, a, b))
    pairs.sort(reverse=True)
    report = {
        "model": args.model,
        "namespaces": len(by_ns),
        "distinct_queries": sum(len(t) for t in by_ns.values()),
        "pairs_within_a_namespace": len(pairs),
        "at_or_above": {str(t): sum(1 for p in pairs if p[0] >= t) for t in THRESHOLDS},
        "closest": [
            {"cosine": round(c, 4), "a": a, "b": b, "same_answer": None}
            for c, _, a, b in pairs[:SAMPLE]
        ],
    }
    json.dump(report, sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
