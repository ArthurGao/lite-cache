"""The L2 columns and the nearest-entry lookup (design §4, S3)."""

import math
from dataclasses import replace
from datetime import timedelta

import psycopg
import pytest

from lite_cache.db import migrate, pending
from lite_cache.errors import StoreError
from lite_cache.store import PgStore
from lite_cache.types import Entry
from tests.conftest import TEST_SCHEMA
from tests.contract.test_store import NOW, entry


def vec(angle: float) -> list[float]:
    """A unit vector in the plane: cosine(vec(a), vec(b)) = cos(a - b)."""
    return [math.cos(angle), math.sin(angle), 0.0]


def semantic(key: str, angle: float, **changes: object) -> Entry:
    base = entry(
        key, question=f"q {key}", partition="p1", embedder="m", question_embedding=vec(angle)
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_migration_two_applies_after_one(pg: psycopg.Connection) -> None:
    assert migrate(pg, TEST_SCHEMA, until="0001_initial") == ["0001_initial"]
    assert pending(pg, TEST_SCHEMA) == ["0002_semantic"]
    assert migrate(pg, TEST_SCHEMA) == ["0002_semantic"]


def test_the_nearest_entry_and_its_similarity(store: PgStore) -> None:
    for key, angle in (("a", 0.0), ("b", math.acos(0.8)), ("c", math.acos(0.1))):
        store.put(semantic(key, angle), NOW)
    found = store.nearest("acme", "p1", "m", vec(0.0), NOW)
    assert found is not None
    best, similarity = found
    assert best.key == "a" and similarity == pytest.approx(1.0)
    found = store.nearest("acme", "p1", "m", vec(math.acos(0.8)), NOW)
    assert found is not None and found[0].key == "b"


@pytest.mark.parametrize(
    "changes",
    [{"scope": "other"}, {"partition": "p2"}, {"embedder": "other-model"}, {"scope": "acme/u1"}],
    ids=["scope", "partition", "embedder", "child-scope"],
)
def test_only_the_same_scope_partition_and_embedder(
    store: PgStore, changes: dict[str, str]
) -> None:
    store.put(semantic("a", 0.0, **changes), NOW)
    assert store.nearest("acme", "p1", "m", vec(0.0), NOW) is None


def test_entries_without_a_vector_and_expired_ones_are_skipped(store: PgStore) -> None:
    store.put(entry("plain"), NOW)  # an L1 entry
    store.put(semantic("old", 0.0, expires_at=NOW + timedelta(seconds=5)), NOW)
    assert store.nearest("acme", "p1", "m", vec(0.0), NOW + timedelta(seconds=10)) is None


def test_a_vector_of_another_dimension_is_a_store_error(store: PgStore) -> None:
    store.put(semantic("a", 0.0), NOW)
    with pytest.raises(StoreError):
        store.nearest("acme", "p1", "m", [1.0, 0.0], NOW)


def test_one_query_for_many_entries(store: PgStore) -> None:
    for i in range(300):
        store.put(semantic(f"k{i}", i / 300), NOW)
    found = store.nearest("acme", "p1", "m", vec(299 / 300), NOW)
    assert found is not None and found[0].key == "k299"
