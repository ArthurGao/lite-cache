"""L2 building blocks (design §3, §5): the injected embedder, calibrated thresholds and the guard.

A threshold belongs to the embedder it was calibrated on (`model_id`): another model's
similarities mean something else (design §0). The guard refuses pairs that look alike but ask
about different numbers or the opposite thing; whatever it cannot see (number words, entities)
is the threshold's job, measured by the pairs evaluation (§8)."""

import re
from importlib.resources import files
from typing import Protocol

from pydantic import TypeAdapter, ValidationError
from pydantic_settings import SettingsError

from lite_cache.config import Settings, Threshold
from lite_cache.errors import ConfigError


class Embedder(Protocol):
    """Injected by the caller (lite-rag passes an adapter over its own embedder). `model_id` must
    cover everything that changes the vectors: provider, model and query prefix."""

    model_id: str
    dim: int

    def embed_query(self, text: str) -> list[float]: ...


_TABLE: TypeAdapter[dict[str, float]] = TypeAdapter(dict[str, Threshold])


def thresholds() -> dict[str, float]:
    """Calibrated thresholds per embedder `model_id`: the packaged table (written only by a
    passing calibration run, §8) with `LITECACHE_SEMANTIC_THRESHOLDS` (environment or `.env`,
    a JSON object) on top."""
    try:
        packaged = _TABLE.validate_json(files("lite_cache").joinpath("thresholds.json").read_text())
        configured = Settings().semantic_thresholds
    except (ValidationError, SettingsError) as exc:
        raise ConfigError(f"invalid L2 thresholds: {exc}") from None
    return {**packaged, **configured}


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_NEGATION = re.compile(
    r"\b(?:not|cannot|no|never|none|nobody|nothing|without)\b|n't|[不没未非别无勿]", re.IGNORECASE
)


def numbers(text: str) -> list[str]:
    """Digit runs, thousands separators removed: `1,000` and `1000` are the same number."""
    return sorted(m.group(0).replace(",", "") for m in _NUMBER.finditer(text))


def guard(q1: str, q2: str) -> str | None:
    """None when the two questions may share an answer; otherwise why not."""
    if numbers(q1) != numbers(q2):
        return "numbers"
    if bool(_NEGATION.search(q1)) != bool(_NEGATION.search(q2)):
        return "negation"
    return None
