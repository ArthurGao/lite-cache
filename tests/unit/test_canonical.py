"""The vendored canonical JSON gives exactly lite-eval's results (design §3.1; test vectors copied
from lite-eval backend/tests/unit/test_canonical_and_dataset.py @ 76dd9a9)."""

import hashlib
import math

import pytest

from lite_cache.canonical import canonical_json, content_hash

# "é" written decomposed (e + combining acute); canonical JSON must give the NFC form U+00E9.
DECOMPOSED_E = "é"


def test_canonical_json_sorts_keys_compacts_and_normalises_nfc() -> None:
    text = canonical_json({"b": [1, 2.5], "a": {"q": DECOMPOSED_E, DECOMPOSED_E: None}})

    assert text == '{"a":{"q":"é","é":null},"b":[1,2.5]}'


def test_canonical_json_refuses_nan() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": math.nan})


def test_content_hash_is_sha256_of_canonical_json() -> None:
    assert content_hash({"a": 1}) == hashlib.sha256(b'{"a":1}').hexdigest()
