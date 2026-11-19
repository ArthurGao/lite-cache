"""What the published wheel and sdist must carry (design §11 S1.5): hosts install the release
wheel by URL, so a missing file shows up only in a host."""

import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def dist(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    out = tmp_path_factory.mktemp("dist")
    # --offline: the build backend comes from uv's cache (`uv sync` put it there).
    subprocess.run(
        ["uv", "build", "--offline", "--quiet", "--out-dir", str(out)],
        cwd=ROOT,
        check=True,
        timeout=180,
    )
    return next(out.glob("lite_cache-*.whl")), next(out.glob("lite_cache-*.tar.gz"))


def test_the_wheel_carries_the_migrations_license_types_and_cli(dist: tuple[Path, Path]) -> None:
    wheel, _ = dist
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    info = f"lite_cache-{version}.dist-info"
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        # open_store reads them with importlib.resources: without them a host cannot migrate.
        shipped = sorted(
            n for n in names if n.startswith("lite_cache/migrations/") and n.endswith(".sql")
        )
        in_repo = sorted(
            f"lite_cache/migrations/{p.name}"
            for p in (ROOT / "src/lite_cache/migrations").glob("*.sql")
        )
        assert shipped == in_repo and shipped
        assert f"{info}/licenses/LICENSE" in names
        assert "Apache License" in zf.read(f"{info}/licenses/LICENSE").decode()
        assert "License-Expression: Apache-2.0" in zf.read(f"{info}/METADATA").decode()
        assert "litecache = lite_cache.cli:app" in zf.read(f"{info}/entry_points.txt").decode()
        # lite-model is not on PyPI: the wheel must name where it comes from, or a host's
        # installer cannot resolve it (a [tool.uv.sources] entry does not reach the metadata).
        requires = [
            line
            for line in zf.read(f"{info}/METADATA").decode().splitlines()
            if line.startswith("Requires-Dist: lite-model")
        ]
        assert len(requires) == 1 and " @ https://github.com/ArthurGao/lite-model/" in requires[0]
        assert "lite_cache/py.typed" in names  # hosts type-check against it (mypy strict)
        assert not [n for n in names if n.startswith("tests/")]


def test_the_sdist_carries_the_license(dist: tuple[Path, Path]) -> None:
    _, sdist = dist
    with tarfile.open(sdist) as tf:
        assert f"{sdist.name.removesuffix('.tar.gz')}/LICENSE" in tf.getnames()
