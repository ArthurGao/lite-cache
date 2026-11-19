"""The L1 key (design §3.1), the scope format and the `cache` value in call_context (§3)."""

import math
from typing import Any

import pytest
from lite_model import Message

from lite_cache.canonical import canonical_json
from lite_cache.errors import InvalidScope
from lite_cache.keys import cache_key, check_scope, policy_from
from lite_cache.types import Policy
from tests.helpers import ASK, a_call

BASE = cache_key(a_call(), "acme")


def test_the_key_is_stable() -> None:
    assert cache_key(a_call(), "acme") == BASE
    assert len(BASE) == 64
    assert all(c in "0123456789abcdef" for c in BASE)


@pytest.mark.parametrize(
    ("scope", "changes"),
    [
        ("acme/u1", {}),
        ("acme", {"role": "graph"}),
        ("acme", {"backend": "deepseek"}),
        ("acme", {"model": "qwen3:1.7b"}),
        ("acme", {"messages": [Message("user", "Hi!")]}),
        ("acme", {"messages": [Message("system", "Hi?")]}),
        ("acme", {"params": {"temperature": 0.5}}),
        ("acme", {"params": {"temperature": 0, "tools": [{"type": "function", "name": "f"}]}}),
        ("acme", {"params": {"temperature": 0, "response_format": {"type": "json_object"}}}),
        ("acme", {"params": {"temperature": 0, "reasoning_effort": "low"}}),
    ],
    ids=[
        "scope",
        "role",
        "backend",
        "model",
        "content",
        "message-role",
        "temperature",
        "tools",
        "response_format",
        "reasoning_effort",
    ],
)
def test_each_part_changes_the_key(scope: str, changes: dict[str, Any]) -> None:
    assert cache_key(a_call(**changes), scope) != BASE


def test_question_is_not_in_the_key() -> None:
    one = a_call(context={"question": "What is the brew ratio?"})
    other = a_call(context={"question": "Something else entirely"})
    assert cache_key(one, "acme") == cache_key(other, "acme") == BASE


def test_decomposed_and_composed_text_share_a_key() -> None:
    decomposed = cache_key(a_call(messages=[Message("user", "café")]), "acme")
    composed = cache_key(a_call(messages=[Message("user", "café")]), "acme")
    plain = cache_key(a_call(messages=[Message("user", "cafe")]), "acme")
    assert decomposed == composed
    assert plain != composed


def test_nan_in_params_raises() -> None:
    with pytest.raises(ValueError):
        cache_key(a_call(params={"temperature": math.nan}), "acme")


@pytest.mark.parametrize("scope", ["acme", "acme/u42", "tenderted/tenant_42/faq/u.1"])
def test_valid_scopes(scope: str) -> None:
    assert check_scope(scope) == scope


@pytest.mark.parametrize("scope", ["", "a//b", "/a", "a/", "a b", "ä", None, 3])
def test_invalid_scopes(scope: object) -> None:
    with pytest.raises(InvalidScope):
        check_scope(scope)


def test_policy_from_context() -> None:
    explicit = Policy(ttl_s=60)
    assert policy_from({}) is None
    assert policy_from({"cache": False}) is None
    assert policy_from({"cache": True}) == Policy()
    assert policy_from({"cache": explicit}) is explicit


@pytest.mark.parametrize("value", ["yes", 1, {"ttl_s": 5}])
def test_any_other_cache_value_is_a_type_error(value: object) -> None:
    with pytest.raises(TypeError):
        policy_from({"cache": value})


@pytest.mark.parametrize("ttl", [0, -5])
def test_a_ttl_must_be_positive(ttl: int) -> None:
    with pytest.raises(ValueError):
        Policy(ttl_s=ttl)


def test_the_hand_made_call_matches_what_lite_model_sends() -> None:
    # The key is over the request as sent; a_call() must build that same request, or keys made
    # from it in tests silently differ (lite-model sends temperature 0.0, a float).
    from tests.helpers import local_model
    from tests.upstream import Upstream

    up = Upstream()
    local_model(up).text("answer", ASK)
    # Compared as canonical JSON, as the key sees them: in Python 0 == 0.0, in the key not.
    assert canonical_json(dict(a_call().request)) == canonical_json(up.body())
