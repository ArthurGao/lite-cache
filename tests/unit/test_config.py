"""Settings (design §5) and the redacted connection string the CLI prints."""

import pytest
from pydantic import ValidationError

from lite_cache.config import Settings


def test_the_default_schema_is_lite_cache() -> None:
    assert Settings().schema_name == "lite_cache"


def test_the_schema_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITECACHE_SCHEMA", "cache_two")
    assert Settings().schema_name == "cache_two"


@pytest.mark.parametrize("name", ["public", "information_schema", "pg_temp", "Bad-Name", "1abc"])
def test_a_shared_system_or_invalid_schema_is_refused(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv("LITECACHE_SCHEMA", name)
    with pytest.raises(ValidationError):
        Settings()


def test_an_unprefixed_schema_variable_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # pydantic-settings reads a field's plain `alias` from the environment without the prefix:
    # `alias="schema"` would have taken any `SCHEMA` variable and ignored LITECACHE_SCHEMA.
    monkeypatch.setenv("SCHEMA", "someone_elses")
    assert Settings().schema_name == "lite_cache"
