from __future__ import annotations

from typing import Any

import pytest

from tennis_worker.errors import UnsupportedStrokeError
from tennis_worker.pipeline.biomechanics.registry import default_registry
from tennis_worker.pipeline.knowledge.repository import (
    PublishedKnowledgeBaseRepository,
    StoredRelease,
    YamlKnowledgeBaseRepository,
)
from tests.conftest import KNOWLEDGE_DIR

SEED = YamlKnowledgeBaseRepository(KNOWLEDGE_DIR).load("forehand")


def snapshot(version: str, **changes: Any) -> dict[str, Any]:
    data = SEED.model_dump(mode="json", by_alias=True, exclude_none=True) | {"version": version}
    return data | changes


class FakeSource:
    def __init__(self, *releases: StoredRelease) -> None:
        self.releases = list(releases)
        self.active_id = releases[-1].id if releases else None
        self.invalid: dict[str, str] = {}
        self.calls = 0
        self.fail = False

    def active(self, stroke: str) -> StoredRelease | None:
        self.calls += 1
        if self.fail:
            raise ConnectionError("db caída")
        return next((r for r in self.releases if r.id == self.active_id), None)

    def latest_valid(self, stroke: str, exclude_id: str) -> StoredRelease | None:
        valid = [r for r in self.releases if r.id != exclude_id and r.id not in self.invalid]
        return valid[-1] if valid else None

    def mark_invalid(self, release_id: str, reason: str) -> None:
        self.invalid[release_id] = reason


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def repo(source: FakeSource, clock: Clock) -> PublishedKnowledgeBaseRepository:
    return PublishedKnowledgeBaseRepository(source, default_registry().metric_ids, refresh_seconds=10, clock=clock)


def test_new_active_release_is_picked_up_after_refresh_without_restart() -> None:
    source, clock = FakeSource(StoredRelease("r1", snapshot("0.2.0"))), Clock()
    knowledge = repo(source, clock)
    assert knowledge.load("forehand").version == "0.2.0"
    source.releases.append(StoredRelease("r2", snapshot("0.3.0")))
    source.active_id = "r2"
    clock.now = 5
    assert knowledge.load("forehand").version == "0.2.0"  # dentro de la ventana: caché, sin consulta
    assert source.calls == 1
    clock.now = 11
    assert knowledge.load("forehand").version == "0.3.0"


def test_invalid_release_is_marked_and_previous_one_is_kept() -> None:
    source, clock = FakeSource(StoredRelease("r1", snapshot("0.2.0"))), Clock()
    knowledge = repo(source, clock)
    knowledge.load("forehand")
    bad_rule = {"id": "X-1", "title": "x", "category": "fundamental", "impact": 1, "difficulty": 1,
                "when": {"all": [{"metric": "no_existe", "op": "lt", "value": 1}]},
                "explanation": "e", "correction": "c"}
    source.releases.append(StoredRelease("r2", snapshot("0.3.0", rules=[bad_rule])))
    source.active_id = "r2"
    clock.now = 11
    assert knowledge.load("forehand").version == "0.2.0"
    assert "no_existe" in source.invalid["r2"]


def test_without_cache_an_invalid_active_release_falls_back_to_latest_valid() -> None:
    source = FakeSource(StoredRelease("r1", snapshot("0.2.0")), StoredRelease("r2", {"stroke": "forehand"}))
    assert repo(source, Clock()).load("forehand").version == "0.2.0"
    assert "r2" in source.invalid


def test_database_outage_uses_cache_but_fails_without_it() -> None:
    source, clock = FakeSource(StoredRelease("r1", snapshot("0.2.0"))), Clock()
    knowledge = repo(source, clock)
    knowledge.load("forehand")
    source.fail, clock.now = True, 11
    assert knowledge.load("forehand").version == "0.2.0"
    with pytest.raises(ConnectionError):
        repo(source, Clock()).load("forehand")


def test_stroke_without_release_is_unsupported() -> None:
    with pytest.raises(UnsupportedStrokeError):
        repo(FakeSource(), Clock()).load("serve")


def test_catalog_lists_every_registered_metric() -> None:
    catalog = default_registry().catalog()
    assert {e.metric_id for e in catalog if e.stroke == "forehand"} == default_registry().metric_ids("forehand")


def test_snapshot_fixture_is_what_the_worker_accepts() -> None:
    """contracts/fixtures/knowledge_base.example.json lo valida también la API (domain/knowledge_test.go):
    lo que la API publica y lo que el worker carga es el mismo formato."""
    import json

    from tests.conftest import CONTRACTS_DIR

    snapshot = json.loads((CONTRACTS_DIR / "fixtures" / "knowledge_base.example.json").read_text(encoding="utf-8"))
    source = FakeSource(StoredRelease("r1", snapshot))
    assert repo(source, Clock()).load("forehand").label == SEED.label
    catalog = json.loads((CONTRACTS_DIR / "fixtures" / "metric_catalog.example.json").read_text(encoding="utf-8"))
    assert {m["id"] for m in catalog if m["stroke"] == "forehand"} == default_registry().metric_ids("forehand")
