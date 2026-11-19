"""L2 building blocks (design §3, §5): the policy flag, the thresholds and the guard."""

import pytest

from lite_cache.errors import ConfigError
from lite_cache.semantic import guard, thresholds
from lite_cache.types import Policy


def test_semantic_is_off_by_default() -> None:
    assert Policy().semantic is False
    assert Policy(semantic=True).semantic is True


def test_thresholds_merge_the_packaged_table_and_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packaged = thresholds()
    monkeypatch.setenv("LITECACHE_SEMANTIC_THRESHOLDS", '{"ollama/m": 0.9}')
    assert thresholds() == {**packaged, "ollama/m": 0.9}


@pytest.mark.parametrize("raw", ['{"m": 1.5}', '{"m": 0}', '{"m": "x"}', "not json", "[0.9]"])
def test_bad_thresholds_are_refused(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("LITECACHE_SEMANTIC_THRESHOLDS", raw)
    with pytest.raises(ConfigError):
        thresholds()


@pytest.mark.parametrize(
    ("q1", "q2", "reason"),
    [
        ("退款要几天", "退款需要多少天", None),
        ("超过1000要审批吗", "超过2000要审批吗", "numbers"),
        ("超过1000要审批吗", "超过 1000 要审批吗", None),
        ("refund over 1,000?", "refund over 1000?", None),
        ("能退货吗", "不能退货吗", "negation"),
        ("Can I return it?", "Can't I return it?", "negation"),
        ("Is it free?", "Is it not free?", "negation"),
        ("Can I keep it?", "Cannot I keep it?", "negation"),
        ("Can I keep it?", "Can I not keep it?", "negation"),
        ("What does E4012 mean?", "What does E4013 mean?", "numbers"),
        ("same question", "same question", None),
    ],
)
def test_the_guard(q1: str, q2: str, reason: str | None) -> None:
    assert guard(q1, q2) == reason
    assert guard(q2, q1) == reason


def test_thresholds_are_read_from_dotenv_too(tmp_path: object) -> None:
    # Design §5: every LITECACHE_ setting comes from the environment or `.env` (the tests run in
    # an empty working directory, see conftest).
    from pathlib import Path

    Path(".env").write_text('LITECACHE_SEMANTIC_THRESHOLDS={"ollama/bge-m3:567m": 0.95}\n')
    assert thresholds()["ollama/bge-m3:567m"] == 0.95
