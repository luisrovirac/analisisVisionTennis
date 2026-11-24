"""Reglas según la vista de cámara y avisos del protocolo de grabación."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from tennis_worker.contracts import JobMessage, MetricValue
from tennis_worker.pipeline.knowledge.engine import RuleEngine
from tennis_worker.pipeline.knowledge.models import KnowledgeBase
from tennis_worker.pipeline.landmarks import LEFT, RIGHT, Landmark
from tennis_worker.pipeline.quality import QualityAssessor
from tests.synthetic import make_pose


def kb(*rules: dict[str, Any]) -> KnowledgeBase:
    return KnowledgeBase.model_validate({"stroke": "forehand", "version": "1.0.0", "rules": list(rules)})


def rule(rule_id: str, **extra: Any) -> dict[str, Any]:
    return {"id": rule_id, "title": "t", "category": "fundamental", "impact": 3, "difficulty": 2,
            "when": {"all": [{"metric": "m", "op": "lt", "value": 1}]}, "explanation": "e", "correction": "c",
            **extra}


METRICS = {"m": MetricValue(id="m", label="M", value=0.0, unit="m", confidence=0.9)}


def test_rule_without_views_applies_to_every_view() -> None:
    base = kb(rule("ANY"))
    for view in ("side", "front", "back", None):
        assert [f.rule.id for f in RuleEngine(0.5).evaluate(base, METRICS, view).fired] == ["ANY"]


def test_rule_is_skipped_outside_its_views() -> None:
    base = kb(rule("SIDE", views=["side"]), rule("FRONT", views=["front", "back"]))
    side = RuleEngine(0.5).evaluate(base, METRICS, "side")
    assert [f.rule.id for f in side.fired] == ["SIDE"]
    assert side.skipped_views == {"FRONT": {"front", "back"}}
    back = RuleEngine(0.5).evaluate(base, METRICS, "back")
    assert [f.rule.id for f in back.fired] == ["FRONT"] and back.skipped_views == {"SIDE": {"side"}}


def test_without_a_view_every_rule_is_evaluated() -> None:
    base = kb(rule("SIDE", views=["side"]))
    assert RuleEngine(0.5).evaluate(base, METRICS).skipped_views == {}


@pytest.mark.parametrize("views", [["side", "side"], ["top"]])
def test_invalid_views_are_rejected(views: list[str]) -> None:
    with pytest.raises(ValidationError):
        kb(rule("BAD", views=views))


# --- Avisos del protocolo de grabación -------------------------------------------------------------------

JOB = JobMessage(schema_version="1.1", analysis_id="a", video_key="k", stroke_type="forehand",
                 handedness="right", camera_view="side")


def test_good_recording_has_no_framing_warnings() -> None:
    assert QualityAssessor(0.6).warnings(make_pose(), JOB, []) == []


def test_low_fps_warning() -> None:
    pose = replace(make_pose(), fps=30.0)
    assert any("30 fps" in w for w in QualityAssessor(0.6).warnings(pose, JOB, []))


def test_small_player_warning() -> None:
    pose = make_pose()
    image = pose.image.copy()
    image[:, :, :2] *= 0.1  # el jugador ocupa una décima parte de la imagen
    warnings = QualityAssessor(0.6).warnings(replace(pose, image=image), JOB, [])
    assert any("pequeño" in w for w in warnings)


def test_cut_off_body_warning() -> None:
    pose = make_pose()
    visibility = pose.visibility.copy()
    visibility[:, [LEFT.ankle, RIGHT.ankle]] = 0.1  # pies fuera de la imagen
    warnings = QualityAssessor(0.6).warnings(replace(pose, visibility=visibility), JOB, [])
    assert any("fuera de la imagen" in w for w in warnings)


def test_framing_ignores_undetected_frames() -> None:
    pose = make_pose()
    detected = np.zeros(pose.frame_count, dtype=bool)
    visibility = pose.visibility.copy()
    visibility[:, Landmark.NOSE] = 0.0
    assert QualityAssessor(0.6)._framing(replace(pose, detected=detected, visibility=visibility)) == []
