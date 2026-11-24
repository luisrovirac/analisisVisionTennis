from __future__ import annotations

import math

import pytest

from tennis_worker.contracts import CameraView, MetricValue
from tennis_worker.pipeline.knowledge.engine import RuleEngine
from tennis_worker.pipeline.knowledge.models import Condition, KnowledgeBase
from tennis_worker.pipeline.knowledge.pro_validation import (
    ProStroke,
    Session,
    beyond,
    check_conditions,
    direction,
    fire_counts,
    leave_one_out,
    pro_bound,
    sessions,
    typical_spread,
)


def metric(value: float, confidence: float = 0.9, metric_id: str = "knee_angle_min") -> MetricValue:
    return MetricValue(id=metric_id, label=metric_id, value=value, unit="deg", confidence=confidence)


def stroke(player: str, value: float, confidence: float = 0.9, view: CameraView = "side") -> ProStroke:
    return ProStroke(player, "forehand", view, {"knee_angle_min": metric(value, confidence)})


def session(player: str, *values: float) -> Session:
    return sessions([stroke(player, v) for v in values], 0.5)[0]


def kb(op: str = "gt", value: float = 160, views: list[str] | None = None) -> KnowledgeBase:
    return KnowledgeBase.model_validate({"stroke": "forehand", "version": "0.0.1", "rules": [
        {"id": "FH-POST-001", "title": "Piernas rígidas", "category": "fundamental", "impact": 4, "difficulty": 1,
         "views": views or [], "when": {"all": [{"metric": "knee_angle_min", "op": op, "value": value}]},
         "explanation": "x", "correction": "y"},
        {"id": "FH-STR-001", "title": "Buena flexión", "category": "fundamental", "polarity": "strength",
         "impact": 3, "difficulty": 1, "when": {"all": [{"metric": "knee_angle_min", "op": "lt", "value": 120}]},
         "explanation": "x", "why_it_matters": "z"},
    ]})


def test_session_is_the_median_of_the_reliable_strokes() -> None:
    [s] = sessions([stroke("A", 100), stroke("A", 110), stroke("A", 150), stroke("A", 300, confidence=0.1)], 0.5)
    assert s.strokes == 4 and s.metrics["knee_angle_min"].value == 110  # el poco fiable no cuenta
    assert s.metrics["knee_angle_min"].confidence == 1.0 and s.samples["knee_angle_min"] == (100, 110, 150)


def test_metric_reliable_in_less_than_half_the_strokes_is_left_out() -> None:
    [s] = sessions([stroke("A", 100), stroke("A", 110, 0.2), stroke("A", 120, 0.2)], 0.5)
    assert "knee_angle_min" not in s.metrics


def test_sessions_group_by_player_and_view() -> None:
    found = sessions([stroke("A", 100), stroke("B", 120), stroke("A", 140, view="front")], 0.5)
    assert [(s.player, s.view, s.strokes) for s in found] == [("A", "front", 1), ("A", "side", 1), ("B", "side", 1)]


def test_direction_comes_from_the_operator() -> None:
    assert direction(Condition(metric="m", op="lt", value=1)) == "low"
    assert direction(Condition(metric="m", op="gte", value=1)) == "high"
    assert direction(Condition(metric="m", op="between", value=(1, 2))) is None


def test_typical_spread_needs_enough_strokes() -> None:
    group = [session("A", 100, 110, 120, 130, 140), session("B", 90, 150), session("C", 100, 100, 100)]
    # A: p90 - p10 = 136 - 104 = 32; C: 0; B no llega a 3 golpes → mediana de [32, 0] = 16
    assert typical_spread(group, "knee_angle_min") == pytest.approx(16)
    assert typical_spread([session("B", 90, 150)], "knee_angle_min") == 0


def test_pro_bound_is_the_extreme_median_with_a_margin() -> None:
    group = [session("A", 100, 110, 120, 130, 140), session("B", 150), session("C", 125)]
    spread = typical_spread(group, "knee_angle_min")
    assert pro_bound(group, "knee_angle_min", "high") == pytest.approx(150 + 0.25 * spread)
    assert pro_bound(group, "knee_angle_min", "low") == pytest.approx(120 - 0.25 * spread)
    assert pro_bound(group[:2], "knee_angle_min", "high") is None  # menos de 3 jugadores


def test_beyond_only_counts_the_defect_side() -> None:
    assert beyond(170, 160, "high") and not beyond(150, 160, "high")
    assert beyond(100, 110, "low") and not beyond(120, 110, "low")


def test_leave_one_out_flags_the_player_outside_the_others() -> None:
    group = [session("A", 120), session("B", 125), session("C", 130), session("D", 170)]
    held = {h.player: h for h in leave_one_out(group, "knee_angle_min", "high")}
    assert held["D"].flagged and held["D"].bound == 130  # sin dispersión (1 golpe por jugador)
    assert not held["A"].flagged and held["A"].bound == 170
    assert leave_one_out(group[:3], "knee_angle_min", "high") == ()  # al quitar uno quedan 2


def test_check_conditions_compares_the_threshold_with_the_pro_bound() -> None:
    group = [session("A", 120), session("B", 140), session("C", 165)]
    [check] = check_conditions(kb("gt", 160), group)
    assert check.bound == 165 and check.too_strict and check.margin == -5  # C saltaría la regla
    [loose] = check_conditions(kb("gt", 170), group)
    assert not loose.too_strict and loose.margin == 5 and loose.held_out == ()


def test_check_conditions_respects_rule_views() -> None:
    group = [session("A", 120), session("B", 140), session("C", 165)]
    assert check_conditions(kb(views=["front"]), group) == []


def test_fire_counts_per_session_and_who() -> None:
    engine = RuleEngine(min_confidence=0.5)
    group = [session("A", 110), session("B", 140), session("C", 165)]
    counts = fire_counts(kb("gt", 160), ((s.player, s.view, s.metrics) for s in group), engine)
    assert (counts["FH-POST-001"].fired, counts["FH-POST-001"].evaluated, counts["FH-POST-001"].who) == (1, 3, ["C"])
    assert counts["FH-STR-001"].fired == 1 and counts["FH-STR-001"].who == ["A"]


def test_fire_counts_skip_unreliable_metrics_and_other_views() -> None:
    engine = RuleEngine(min_confidence=0.5)
    items = [("x", "side", {"knee_angle_min": metric(170, 0.1)}), ("y", "side", {"knee_angle_min": metric(170)})]
    counts = fire_counts(kb("gt", 160, views=["side"]), items, engine)
    assert (counts["FH-POST-001"].fired, counts["FH-POST-001"].evaluated) == (1, 1)
    front = fire_counts(kb("gt", 160, views=["side"]), [("z", "front", {"knee_angle_min": metric(170)})], engine)
    assert front["FH-POST-001"].evaluated == 0 and math.isnan(front["FH-POST-001"].rate)


def test_a_gate_condition_alone_is_not_too_strict() -> None:
    """«Rodilla flexionada y sin empuje»: que los profesionales flexionen no castiga a nadie."""
    rules = kb().model_dump(by_alias=True)
    rules["rules"][0]["when"] = {"all": [{"metric": "knee_angle_min", "op": "lt", "value": 150},
                                         {"metric": "leg_drive", "op": "lt", "value": 8}]}
    group = [session("A", 120), session("B", 140), session("C", 165)]
    [gate, drive] = check_conditions(KnowledgeBase.model_validate(rules), group)
    assert gate.combined and gate.bound == 120 and not gate.too_strict
    assert drive.bound is None  # nadie tiene leg_drive
