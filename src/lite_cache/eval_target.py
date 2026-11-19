"""The L2 pairs evaluation's lite-eval target (design §8, S3b; needs the `eval` extra).

For each labelled pair it embeds both questions with the embedder under calibration and reports
their cosine, which depends on the embedder alone. It decides nothing: `eval/pairs/calibrate.py`
applies the current guard and chooses the threshold on dev, gates it on test (`lite_cache.pairs`),
so one run serves every threshold and every guard. lite-eval caches the outputs: a re-run, or a
guard change, embeds nothing again."""

import asyncio
import json
import math
import urllib.request
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, Literal

from lite_eval.contracts import Case, Output

from lite_cache.canonical import sha256_hex

if TYPE_CHECKING:
    from lite_eval.datasets.local import Dataset

OLLAMA = "http://127.0.0.1:11434"


def embedder_id(provider: str, model: str, query_prefix: str = "") -> str:
    """The `Embedder.model_id` convention (design §3): `provider/model`, plus `#` and 8 hex of
    the query prefix's sha256 when there is one (a prefix changes every vector)."""
    base = f"{provider}/{model}"
    if not query_prefix:
        return base
    return f"{base}#{sha256_hex(query_prefix)[:8]}"


class OllamaEmbedder:
    """An `Embedder` over Ollama's /api/embed: what lite-rag's default (`bge-m3:567m`, no
    prefixes) computes, for calibrating its threshold."""

    def __init__(self, model: str = "bge-m3:567m", dim: int = 1024, url: str = OLLAMA) -> None:
        self.model = model
        self.dim = dim
        self.url = url
        self.model_id = embedder_id("ollama", model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        body = json.dumps({"model": self.model, "input": texts}).encode()
        request = urllib.request.Request(
            f"{self.url}/api/embed", data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            vectors: list[list[float]] = json.load(response)["embeddings"]
        if any(len(v) != self.dim for v in vectors):
            raise ValueError(f"{self.model_id} returned a vector of another size than {self.dim}")
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


class PairsTarget:
    """lite-eval target: `{"q1", "q2"}` in, `{"cosine"}` out."""

    name = "lite-cache-pairs"

    def __init__(self, model: str = "bge-m3:567m", dim: int = 1024) -> None:
        self.embedder = OllamaEmbedder(model, dim)

    def config(self) -> dict[str, Any]:
        return {"embedder": self.embedder.model_id}

    def versions(self) -> dict[str, str]:
        return {"lite-cache": version("lite-cache")}

    def info(self) -> dict[str, str]:
        return {"ollama": self.embedder.url}

    def billing(self) -> Literal["free"]:
        return "free"

    def setup_identity(self) -> dict[str, Any]:
        return {}

    async def setup(self, dataset: "Dataset") -> None:
        return None

    async def run(self, case: Case) -> Output:
        pair: dict[str, str] = case.input  # type: ignore[assignment]
        a, b = await asyncio.to_thread(self.embedder.embed, [pair["q1"], pair["q2"]])
        return Output(case_id=case.id, value={"cosine": cosine(a, b)})

    async def teardown(self) -> None:
        return None


def pairs(model: str = "bge-m3:567m", dim: int = 1024) -> PairsTarget:
    """The suite's entry point (`python: lite_cache.eval_target:pairs`)."""
    return PairsTarget(model, dim)
