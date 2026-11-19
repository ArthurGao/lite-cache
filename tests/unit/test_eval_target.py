"""The pairs evaluation's lite-eval target (design §8, S3b) against a stub of Ollama's /api/embed
(the network boundary; no real model)."""

import asyncio
import json
import math
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

import pytest
from lite_eval.contracts import Case

from lite_cache.eval_target import PairsTarget, cosine, embedder_id
from tests.helpers import FakeVerifier

VECTORS = {"refund time": [1.0, 0.0], "how long is a refund": [0.8, 0.6], "1000": [0.0, 1.0]}


class _Embed(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Embed.seen.append({"path": self.path, **body})
        reply = json.dumps({"embeddings": [VECTORS[t] for t in body["input"]]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args: object) -> None:
        return None


@pytest.fixture
def ollama() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Embed)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Embed.seen = []
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _target(url: str, dim: int = 2) -> PairsTarget:
    target = PairsTarget("bge-m3:567m", dim)
    target.embedder.url = url
    return target


def test_a_pair_becomes_its_cosine(ollama: str) -> None:
    # Only the cosine: it depends on the embedder alone, so a guard change re-uses lite-eval's
    # cached outputs (calibrate.py applies the current guard).
    target = _target(ollama)
    case = Case(id="c1", input={"q1": "refund time", "q2": "how long is a refund"})
    out = asyncio.run(target.run(case))
    assert out.case_id == "c1"
    assert isinstance(out.value, dict) and set(out.value) == {"cosine"}
    assert out.value["cosine"] == pytest.approx(0.8)
    # one request with both questions, to the model under calibration
    assert _Embed.seen == [
        {
            "path": "/api/embed",
            "model": "bge-m3:567m",
            "input": ["refund time", "how long is a refund"],
        }
    ]
    assert target.config() == {"embedder": "ollama/bge-m3:567m"}
    assert target.billing() == "free"


def test_a_vector_of_another_size_fails(ollama: str) -> None:
    with pytest.raises(ValueError, match="size"):
        asyncio.run(
            _target(ollama, dim=3).run(Case(id="c3", input={"q1": "refund time", "q2": "1000"}))
        )


def test_embedder_id_names_a_query_prefix() -> None:
    assert embedder_id("ollama", "bge-m3:567m") == "ollama/bge-m3:567m"
    tagged = embedder_id("ollama", "e5", "query: ")
    assert tagged.startswith("ollama/e5#") and len(tagged.split("#")[1]) == 8
    assert tagged != embedder_id("ollama", "e5", "search_query: ")


def test_cosine() -> None:
    assert cosine([3.0, 4.0], [3.0, 4.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 2.0]) == pytest.approx(0.0)
    assert cosine([1.0, 1.0], [1.0, 0.0]) == pytest.approx(math.sqrt(0.5))


def test_the_verified_target_judges_only_from_the_floor_up(ollama: str) -> None:
    # Review Focus 4: below 0.80 no floor reaches the pair, so no verdict is asked for.
    from lite_cache.eval_target import VerifiedPairsTarget

    judge = FakeVerifier(False)
    target = VerifiedPairsTarget("bge-m3:567m", 2, verifier=judge)
    target.embedder.url = ollama
    near = asyncio.run(
        target.run(Case(id="n", input={"q1": "refund time", "q2": "how long is a refund"}))
    )
    far = asyncio.run(target.run(Case(id="f", input={"q1": "refund time", "q2": "1000"})))
    assert near.value == {"cosine": pytest.approx(0.8), "same_answer": False}
    assert far.value == {"cosine": pytest.approx(0.0), "same_answer": None}
    assert len(judge.calls) == 1


def test_the_verified_config_names_embedder_and_verifier_not_the_guard() -> None:
    # Review Focus 5: a guard change must reuse lite-eval's cached outputs.
    from lite_cache.eval_target import VerifiedPairsTarget

    target = VerifiedPairsTarget("bge-m3:567m", 1024, verifier=FakeVerifier())
    assert target.config() == {"embedder": "ollama/bge-m3:567m", "verifier": "fake-judge"}
