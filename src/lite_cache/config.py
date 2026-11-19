"""Settings (design §5): environment variables with the `LITECACHE_` prefix, or `.env`."""

from typing import Annotated

from pydantic import AfterValidator, Field, StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict


def own_schema(name: str) -> str:
    """lite-cache keeps its tables in a schema of its own: never a shared or system one
    (copied from lite-mem `config.own_schema`, design §5)."""
    if name in ("public", "information_schema") or name.startswith("pg_"):
        raise ValueError(
            f"schema {name!r} is shared or a system schema: use one of lite-cache's own"
        )
    return name


# A schema lite-cache owns: a lowercase SQL identifier, not public or a system schema.
OwnSchema = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z_][a-z0-9_]{0,62}$"),
    AfterValidator(own_schema),
]


# An L2 threshold: a cosine similarity in (0, 1], only ever from a calibration run (design §8).
Threshold = Annotated[float, Field(gt=0, le=1)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LITECACHE_", env_file=".env", extra="ignore", populate_by_name=True
    )

    database_url: str | None = None  # required by open_store (StoreError when missing)
    # Read only from LITECACHE_SCHEMA: a plain alias would make pydantic-settings read an
    # unprefixed SCHEMA variable instead (design §5).
    schema_name: OwnSchema = Field("lite_cache", validation_alias="LITECACHE_SCHEMA")
    # L2 thresholds per embedder model_id (JSON), on top of the packaged calibrated table (§5).
    semantic_thresholds: dict[str, Threshold] = Field(default_factory=dict)
