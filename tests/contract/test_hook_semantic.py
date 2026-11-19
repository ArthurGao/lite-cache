"""L2 in `CacheHook` (design §2 step 4, §3, S3): a real LiteModel on a recording transport and an
embedder that maps known questions to fixed vectors."""

import math
from typing import Any

import pytest
from lite_model import LiteModel, Message, call_context
from pydantic import BaseModel

from lite_cache import CacheHook, ConfigError, EmbedderError, Policy, StoreError
from lite_cache.store import PgStore
from tests.contract.test_hook import USAGE, Clock
from tests.helpers import local_model
from tests.upstream import Upstream, completion

SAME = "How long do refunds take?"
PARAPHRASE = "How many days does a refund take?"  # cosine 0.995 with SAME
FAR = "Where is the office?"  # cosine 0.0
OTHER_NUMBER = "Do refunds over 1000 take long?"
OTHER_NUMBER_2 = "Do refunds over 2000 take long?"  # cosine 0.999 with OTHER_NUMBER, numbers differ
VECTORS = {
    SAME: 0.0,
    PARAPHRASE: math.acos(0.995),
    FAR: math.pi / 2,
    OTHER_NUMBER: 1.0,
    OTHER_NUMBER_2: 1.0 + math.acos(0.999),
}


class Days(BaseModel):
    days: int | None = None  # any payload validates: the kind check must stop it


class FakeEmbedder:
    model_id = "fake/vec3"
    dim = 3

    def __init__(self, *, fail: bool = False, dim: int = 3) -> None:
        self.calls: list[str] = []
        self._fail = fail
        self._dim = dim

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        if self._fail:
            raise RuntimeError("embedding service down")
        a = VECTORS[text]
        return [math.cos(a), math.sin(a), 0.0][: self._dim]


THRESHOLDS = {"fake/vec3": 0.99}
Setup = tuple[LiteModel, Upstream, CacheHook, FakeEmbedder]


def ask(model: LiteModel, text: str, **context: Any) -> Any:
    """`text` as the message; by default also the `question` in the call context. A context
    value of None is left out (a call without that key)."""
    values: dict[str, Any] = {
        "scope": "acme",
        "cache": Policy(semantic=True),
        "question": text,
        "partition": "p1",
        **context,
    }
    with call_context(**{k: v for k, v in values.items() if v is not None}):
        return model.text("answer", [Message("user", text)])


@pytest.fixture
def setup(store: PgStore) -> Setup:
    up = Upstream((200, completion("seven days", usage=USAGE)))
    model = local_model(up)
    embedder = FakeEmbedder()
    hook = CacheHook(store, clock=Clock(), embedder=embedder, thresholds=THRESHOLDS)
    model.hooks.register(hook)
    return model, up, hook, embedder


def test_a_similar_question_is_answered_from_the_cache(setup: Setup) -> None:
    model, up, hook, embedder = setup
    first = ask(model, SAME)
    second = ask(model, PARAPHRASE)
    assert len(up.requests) == 1
    assert second.cached and second.text == first.text
    assert (second.provenance, second.usage) == (first.provenance, first.usage)
    [stats] = hook.stats()
    assert (stats.hits, stats.semantic_hits, stats.stored) == (0, 1, 1)
    assert stats.hit_rate == 0.5
    assert embedder.calls == [SAME, PARAPHRASE]  # once per lookup, not again when writing


@pytest.mark.parametrize(
    "changes",
    [{"partition": "p2"}, {"scope": "acme/u2"}],
    ids=["partition", "scope"],
)
def test_another_partition_or_scope_misses(setup: Setup, changes: dict[str, str]) -> None:
    model, up, _, _ = setup
    ask(model, SAME)
    assert not ask(model, PARAPHRASE, **changes).cached
    assert len(up.requests) == 2


def test_below_the_threshold_misses(setup: Setup) -> None:
    model, up, _, _ = setup
    ask(model, SAME)
    assert not ask(model, FAR).cached
    assert len(up.requests) == 2


def test_the_guard_refuses_another_number(setup: Setup) -> None:
    model, up, _, _ = setup
    ask(model, OTHER_NUMBER)
    assert not ask(model, OTHER_NUMBER_2).cached  # similar enough, but 1000 is not 2000
    assert len(up.requests) == 2


@pytest.mark.parametrize(
    ("policy", "question", "partition"),
    [
        (Policy(), SAME, "p1"),
        (Policy(semantic=True), None, "p1"),
        (Policy(semantic=True), SAME, None),
    ],
    ids=["not-semantic", "no-question", "no-partition"],
)
def test_without_semantic_question_or_partition_it_is_l1_only(
    setup: Setup, policy: Policy, question: str | None, partition: str | None
) -> None:
    model, up, _, embedder = setup
    for q in (SAME, PARAPHRASE):
        ask(model, q, cache=policy, question=question, partition=partition)
    assert len(up.requests) == 2
    assert embedder.calls == []


def test_an_l1_hit_never_embeds(setup: Setup) -> None:
    model, up, _, embedder = setup
    ask(model, SAME)
    assert ask(model, SAME).cached
    assert embedder.calls == [SAME]
    assert len(up.requests) == 1


def test_an_embedder_without_a_calibrated_threshold_fails_at_construction(
    store: PgStore,
) -> None:
    with pytest.raises(ConfigError, match="fake/vec3"):
        CacheHook(store, clock=Clock(), embedder=FakeEmbedder(), thresholds={})


def test_semantic_without_an_embedder_fails_on_every_call(store: PgStore) -> None:
    # Before the L1 lookup and the refresh check: a cache that keeps hitting L1, or refreshes,
    # still says the call cannot be served as asked (design §0).
    up = Upstream()
    model = local_model(up)
    model.hooks.register(CacheHook(store, clock=Clock(), thresholds=THRESHOLDS))
    for policy in (Policy(semantic=True), Policy(semantic=True, refresh=True)):
        with pytest.raises(ConfigError):
            ask(model, SAME, cache=policy)
    assert up.requests == []


def test_an_l2_hit_keeps_no_vector(setup: Setup) -> None:
    # after() does not run for a hit: the lookup's vector must not be kept (it holds the call)
    model, _, hook, _ = setup
    ask(model, SAME)
    ask(model, PARAPHRASE)
    assert len(hook._vectors) == 0


@pytest.mark.parametrize(
    "broken", [FakeEmbedder(fail=True), FakeEmbedder(dim=2)], ids=["raises", "wrong-dim"]
)
def test_an_embedder_failure_is_a_read_failure(store: PgStore, broken: FakeEmbedder) -> None:
    up = Upstream()
    model = local_model(up)
    model.hooks.register(CacheHook(store, clock=Clock(), embedder=broken, thresholds=THRESHOLDS))
    with pytest.raises(EmbedderError):  # a StoreError: the call fails unsent
        ask(model, SAME)
    assert up.requests == []
    assert issubclass(EmbedderError, StoreError)


class NonFinite(FakeEmbedder):
    def embed_query(self, text: str) -> list[float]:
        return [math.nan, 0.0, 1.0] if "nan" in text else [math.inf, 0.0, 0.0]


@pytest.mark.parametrize("text", ["nan please", "inf please"])
def test_a_non_finite_vector_is_an_embedder_error(store: PgStore, text: str) -> None:
    # Codex S3a: not a database error from the lookup, and not swallowed on a refresh write.
    up = Upstream()
    model = local_model(up)
    model.hooks.register(
        CacheHook(store, clock=Clock(), embedder=NonFinite(), thresholds=THRESHOLDS)
    )
    with pytest.raises(EmbedderError, match="finite"):
        ask(model, text)
    assert up.requests == []


class Zero(FakeEmbedder):
    def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0]


def test_a_zero_vector_is_an_embedder_error(store: PgStore) -> None:
    up = Upstream()
    model = local_model(up)
    model.hooks.register(CacheHook(store, clock=Clock(), embedder=Zero(), thresholds=THRESHOLDS))
    with pytest.raises(EmbedderError, match="zero"):
        ask(model, SAME)
    assert up.requests == []


def test_another_role_misses(setup: Setup) -> None:
    model, up, _, _ = setup
    ask(model, SAME)
    with call_context(
        scope="acme", cache=Policy(semantic=True), question=PARAPHRASE, partition="p1"
    ):
        assert not model.text("graph", [Message("user", PARAPHRASE)]).cached
    assert len(up.requests) == 2


def test_a_text_entry_does_not_answer_a_structured_call(store: PgStore) -> None:
    # Review Focus 5: an L2 candidate of the other kind is a miss, not a crash.
    up = Upstream(
        (200, completion("seven days", usage=USAGE)),
        (200, completion('{"days": 7}', usage=USAGE)),
    )
    model = local_model(up)
    model.hooks.register(
        CacheHook(store, clock=Clock(), embedder=FakeEmbedder(), thresholds=THRESHOLDS)
    )
    ask(model, SAME)
    with call_context(
        scope="acme", cache=Policy(semantic=True), question=PARAPHRASE, partition="p1"
    ):
        reply = model.structured("answer", [Message("user", PARAPHRASE)], Days)
    assert not reply.cached and reply.value.days == 7
    assert len(up.requests) == 2


def test_a_refresh_writes_the_vector(setup: Setup) -> None:
    # refresh=True skips the lookup, so the write embeds the question itself; a later paraphrase
    # then hits.
    model, up, _, embedder = setup
    ask(model, SAME, cache=Policy(semantic=True, refresh=True))
    assert ask(model, PARAPHRASE).cached
    assert len(up.requests) == 1
    assert embedder.calls == [SAME, PARAPHRASE]
