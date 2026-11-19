"""The README stays in step with the code: every CLI command and every setting is documented."""

from pathlib import Path

import typer.main

from lite_cache.cli import app
from lite_cache.config import Settings

README = (Path(__file__).resolve().parents[2] / "README.md").read_text()


def test_every_cli_command_is_in_the_readme() -> None:
    commands = typer.main.get_command(app).commands  # type: ignore[attr-defined]
    missing = [name for name in commands if f"litecache {name}" not in README]
    assert sorted(commands) == ["config", "invalidate", "migrate", "purge", "stats"]
    assert missing == []


def test_every_setting_is_in_the_readme() -> None:
    names = [
        "LITECACHE_DATABASE_URL",
        "LITECACHE_SCHEMA",
        "LITECACHE_SEMANTIC_THRESHOLDS",
        "LITECACHE_TEST_DATABASE_URL",
    ]
    assert set(Settings.model_fields) == {"database_url", "schema_name", "semantic_thresholds"}
    assert [n for n in names if f"`{n}`" not in README] == []


def test_the_readme_pins_the_release_it_documents() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[2]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    assert f"releases/download/v{version}/lite_cache-{version}-py3-none-any.whl" in README
