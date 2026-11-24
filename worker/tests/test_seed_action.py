"""Cuándo la semilla YAML puede publicar una versión en Postgres (sin pisar el trabajo de los expertos)."""

from __future__ import annotations

import pytest

from tennis_worker.infrastructure.knowledge_store import SeedState, seed_action

UNTOUCHED = SeedState(has_releases=True, active_version="0.2.0", touched_in_portal=False, seed_version_exists=False)


def test_first_seed_is_inserted() -> None:
    state = SeedState(has_releases=False, active_version=None, touched_in_portal=False, seed_version_exists=False)
    assert seed_action(state, "0.1.0") == "insert"


def test_untouched_seed_is_upgraded_to_a_newer_yaml() -> None:
    assert seed_action(UNTOUCHED, "0.3.0") == "upgrade"
    assert seed_action(UNTOUCHED, "1.0.0") == "upgrade"
    assert seed_action(UNTOUCHED, "0.10.0") == "upgrade"  # comparación numérica, no de texto


@pytest.mark.parametrize("version", ["0.2.0", "0.1.9", "abc"])
def test_same_older_or_malformed_yaml_version_is_kept(version: str) -> None:
    assert seed_action(UNTOUCHED, version) == "keep"


def test_never_overrides_work_done_in_the_portal() -> None:
    touched = SeedState(has_releases=True, active_version="0.2.0", touched_in_portal=True, seed_version_exists=False)
    assert seed_action(touched, "0.3.0") == "keep"


def test_respects_a_rollback_to_an_older_seed() -> None:
    rolled_back = SeedState(has_releases=True, active_version="0.2.0", touched_in_portal=False,
                            seed_version_exists=True)
    assert seed_action(rolled_back, "0.3.0") == "keep"


def test_keeps_when_no_version_is_active() -> None:
    inactive = SeedState(has_releases=True, active_version=None, touched_in_portal=False, seed_version_exists=False)
    assert seed_action(inactive, "0.3.0") == "keep"
