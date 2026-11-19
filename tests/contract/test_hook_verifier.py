"""The L2 verifier in `CacheHook` (design §13, S3c): a candidate that passed the threshold and the
guard is a hit only when the verifier says the two questions share an answer."""

from dataclasses import replace

import pytest
from lite_model import LiteModel

from lite_cache import CacheHook, ConfigError, Policy, StoreError, VerifierError
from lite_cache.store import PgStore
from tests.contract.test_hook import USAGE, Clock
from tests.contract.test_hook_semantic import (
    FAR,
    OTHER_NUMBER,
    OTHER_NUMBER_2,
    PARAPHRASE,
    SAME,
    VECTORS,
    FakeEmbedder,
    ask,
)
from tests.contract.test_semantic_store import semantic
from tests.contract.test_store import NOW
from tests.helpers import FakeVerifier, local_model
from tests.upstream import Upstream, completion

THRESHOLDS = {"fake/vec3+fake-judge": 0.99}


def _setup(store: PgStore, verifier: FakeVerifier) -> tuple[LiteModel, Upstream, CacheHook]:
    up = Upstream((200, completion("seven days", usage=USAGE)))
    model = local_model(up)
    hook = CacheHook(
        store, clock=Clock(), embedder=FakeEmbedder(), verifier=verifier, thresholds=THRESHOLDS
    )
    model.hooks.register(hook)
    return model, up, hook


def test_a_same_verdict_is_a_hit(store: PgStore) -> None:
    verifier = FakeVerifier(True)
    model, up, hook = _setup(store, verifier)
    ask(model, SAME)
    assert ask(model, PARAPHRASE).cached
    assert len(up.requests) == 1
    assert verifier.calls == [(PARAPHRASE, SAME)]
    [stats] = hook.stats()
    assert (stats.semantic_hits, stats.stored) == (1, 1)


def test_a_different_verdict_is_a_miss_counted_in_memory(store: PgStore) -> None:
    verifier = FakeVerifier(False)
    model, up, hook = _setup(store, verifier)
    ask(model, SAME)
    assert not ask(model, PARAPHRASE).cached
    assert len(up.requests) == 2
    assert hook.verifier_rejects == 1
    [stats] = hook.stats()  # one row per call (§3.4): two stored, no extra outcome
    assert (stats.semantic_hits, stats.stored, stats.skips) == (0, 2, {})


def test_the_verifier_is_not_asked_below_the_floor_or_after_the_guard(store: PgStore) -> None:
    verifier = FakeVerifier(True)
    model, _, _ = _setup(store, verifier)
    ask(model, SAME)
    ask(model, FAR)  # cosine 0.0: below the floor
    ask(model, OTHER_NUMBER)
    ask(model, OTHER_NUMBER_2)  # cosine 0.999, but the guard sees 1000 vs 2000
    assert verifier.calls == []


def test_a_verifier_failure_fails_the_call_unsent(store: PgStore) -> None:
    verifier = FakeVerifier(fail=True)
    model, up, _ = _setup(store, verifier)
    ask(model, SAME)
    with pytest.raises(VerifierError, match="fake-judge"):
        ask(model, PARAPHRASE)
    assert len(up.requests) == 1  # only the first call was sent
    assert issubclass(VerifierError, StoreError)


def test_thresholds_are_keyed_by_embedder_and_verifier(store: PgStore) -> None:
    with pytest.raises(ConfigError, match=r"fake/vec3\+fake-judge"):
        CacheHook(
            store,
            clock=Clock(),
            embedder=FakeEmbedder(),
            verifier=FakeVerifier(),
            thresholds={"fake/vec3": 0.99},  # the embedder-only threshold does not apply
        )


def test_an_entry_without_a_question_is_a_miss(store: PgStore) -> None:
    # Review Focus 1: a row with a vector but no question (written by another tool) is never
    # shown to the verifier.
    verifier = FakeVerifier(True)
    model, up, _ = _setup(store, verifier)
    row = semantic("orphan", VECTORS[SAME], embedder="fake/vec3", question=None)
    store.put(replace(row, scope="acme"), NOW)
    assert not ask(model, PARAPHRASE).cached
    assert verifier.calls == []
    assert len(up.requests) == 1


def test_without_a_verifier_l2_is_as_before(store: PgStore) -> None:
    up = Upstream((200, completion("seven days", usage=USAGE)))
    model = local_model(up)
    model.hooks.register(
        CacheHook(store, clock=Clock(), embedder=FakeEmbedder(), thresholds={"fake/vec3": 0.99})
    )
    ask(model, SAME)
    assert ask(model, PARAPHRASE, cache=Policy(semantic=True)).cached


class SaysNo(FakeVerifier):
    """A host's own verifier that hands back the model's raw text: truthy, but not a verdict."""

    def same_answer(self, question: str, candidate: str) -> bool:
        self.calls.append((question, candidate))
        return "no"  # type: ignore[return-value]


def test_a_verdict_that_is_not_a_bool_is_a_verifier_error(store: PgStore) -> None:
    # Final review: "no" is truthy; read as "same" it would serve another question's answer.
    model, up, _ = _setup(store, SaysNo())
    ask(model, SAME)
    with pytest.raises(VerifierError, match="str"):
        ask(model, PARAPHRASE)
    assert len(up.requests) == 1
