"""Métricas de la ampliación: forehand (nuevas), revés, saque y volea.

Las métricas que dependen de la forma del swing se prueban con el generador sintético (swing bueno vs
malo). Las del saque y la volea se prueban sobre poses construidas a mano con geometría conocida y un
Segment explícito, para no depender del segmentador.
"""

from __future__ import annotations

from dataclasses import replace
from typing import get_args

import numpy as np
import pytest

from tennis_worker.contracts import MetricValue, Segment, StrokeType
from tennis_worker.pipeline.biomechanics import metrics as m
from tennis_worker.pipeline.biomechanics.base import Metric, MetricContext
from tennis_worker.pipeline.biomechanics.registry import default_registry
from tennis_worker.pipeline.landmarks import LEFT, RIGHT, Landmark
from tennis_worker.pipeline.segmentation import WristSpeedSegmenter
from tennis_worker.pipeline.types import LANDMARK_COUNT, FloatArray, PoseSequence
from tests.synthetic import BAD_SWING, GOOD_SWING, SwingSpec, make_pose

FPS = 60.0
IMPACT = 70
SEGMENT = Segment(fps=FPS, start_frame=10, backswing_frame=40, impact_frame=IMPACT, end_frame=130)


def swing_metrics(spec: SwingSpec, stroke: StrokeType = "forehand") -> dict[str, MetricValue]:
    pose = make_pose(spec)
    segment = WristSpeedSegmenter().segment(pose, "right")
    return default_registry().compute_all(stroke, MetricContext(pose, segment, "right"))


@pytest.fixture(scope="module")
def good() -> dict[str, MetricValue]:
    return swing_metrics(GOOD_SWING)


@pytest.fixture(scope="module")
def bad() -> dict[str, MetricValue]:
    return swing_metrics(BAD_SWING)


def pose_from_world(world: FloatArray, visibility: float = 0.9) -> PoseSequence:
    frames = world.shape[0]
    return PoseSequence(fps=FPS, image=world.copy(), world=world,
                        visibility=np.full((frames, LANDMARK_COUNT), visibility),
                        detected=np.ones(frames, dtype=bool))


def compute(metric: Metric, world: FloatArray, segment: Segment = SEGMENT) -> MetricValue:
    return metric.compute(MetricContext(pose_from_world(world), segment, "right"))


def standing_world(frames: int = 140) -> FloatArray:
    """Jugador quieto de frente: hombros (±0.2, -0.5), caderas (±0.15, 0), tobillos (±0.25, 0.9)."""
    world = np.zeros((frames, LANDMARK_COUNT, 3))
    for side, sign in ((LEFT, -1.0), (RIGHT, 1.0)):
        world[:, side.shoulder] = [sign * 0.2, -0.5, 0.0]
        world[:, side.elbow] = [sign * 0.25, -0.25, 0.0]
        world[:, side.wrist] = [sign * 0.3, 0.0, 0.0]
        world[:, side.hip] = [sign * 0.15, 0.0, 0.0]
        world[:, side.knee] = [sign * 0.2, 0.45, 0.0]
        world[:, side.ankle] = [sign * 0.25, 0.9, 0.0]
    world[:, Landmark.NOSE] = [0.0, -0.75, 0.05]
    return world


# --- Registro ----------------------------------------------------------------------------------------

def test_every_stroke_type_has_metrics() -> None:
    registry = default_registry()
    for stroke in get_args(StrokeType):
        assert registry.supports(stroke), stroke
        assert len(registry.metric_ids(stroke)) >= 7, stroke


@pytest.mark.parametrize("stroke", get_args(StrokeType))
def test_all_metrics_are_finite_for_every_stroke(stroke: StrokeType) -> None:
    values = swing_metrics(GOOD_SWING, stroke)
    assert set(values) == default_registry().metric_ids(stroke)
    assert all(np.isfinite(v.value) and 0.0 <= v.confidence <= 1.0 for v in values.values())


def test_catalog_lists_metrics_of_every_stroke() -> None:
    catalog = default_registry().catalog()
    assert {e.stroke for e in catalog} == set(get_args(StrokeType))
    assert all(e.label and e.unit for e in catalog)


# --- Forehand / revés (generador sintético) -------------------------------------------------------------

def test_stance_width_ratio(good: dict[str, MetricValue], bad: dict[str, MetricValue]) -> None:
    assert good["stance_width_ratio"].value == pytest.approx(GOOD_SWING.stance_width / 0.4, abs=0.01)
    assert bad["stance_width_ratio"].value == pytest.approx(BAD_SWING.stance_width / 0.4, abs=0.01)


def test_head_stability(good: dict[str, MetricValue], bad: dict[str, MetricValue]) -> None:
    assert good["head_stability"].value == pytest.approx(0.0, abs=1e-6)
    assert bad["head_stability"].value > 0.25  # 0.15 m de balanceo / torso de 0.5 m


def test_racket_drop_below_contact(good: dict[str, MetricValue], bad: dict[str, MetricValue]) -> None:
    assert good["racket_drop_below_contact"].value > 0.15
    assert bad["racket_drop_below_contact"].value < 0.05


def test_leg_drive(good: dict[str, MetricValue], bad: dict[str, MetricValue]) -> None:
    assert good["leg_drive"].value > 20
    assert bad["leg_drive"].value == pytest.approx(0.0, abs=0.5)


def test_off_arm_extension(good: dict[str, MetricValue], bad: dict[str, MetricValue]) -> None:
    assert good["off_arm_extension"].value == pytest.approx(GOOD_SWING.off_arm_reach, abs=1e-6)
    assert bad["off_arm_extension"].value == pytest.approx(BAD_SWING.off_arm_reach, abs=1e-6)


def shoulder_turn_world(yaw_deg: FloatArray) -> FloatArray:
    world = standing_world(len(yaw_deg))
    rad = np.radians(yaw_deg)
    offset = np.stack([np.cos(rad), np.zeros_like(rad), np.sin(rad)], axis=-1) * 0.2
    center = np.array([0.0, -0.5, 0.0])
    world[:, LEFT.shoulder] = center - offset
    world[:, RIGHT.shoulder] = center + offset
    return world


def test_unit_turn_lead_time_separates_early_and_late_preparation() -> None:
    t = np.arange(140, dtype=float)
    early = np.interp(t, [0, 66, 72], [-90, -90, 0])  # girado desde el principio de la ventana
    late = np.interp(t, [0, 58, 64, 72], [0, 0, -90, 0])  # gira en el último momento
    metric = m.UnitTurnLeadTime()
    assert compute(metric, shoulder_turn_world(early)).value == pytest.approx(1.0)
    assert compute(metric, shoulder_turn_world(late)).value == pytest.approx(0.1)


def test_unit_turn_lead_time_is_zero_without_turn() -> None:
    assert compute(m.UnitTurnLeadTime(), standing_world()).value == 0.0


def test_wrist_separation_distinguishes_two_handed_backhand() -> None:
    world = standing_world()
    one_handed = compute(m.WristSeparationAtImpact(), world)
    world[IMPACT, LEFT.wrist] = world[IMPACT, RIGHT.wrist] + [-0.08, 0.0, 0.0]
    two_handed = compute(m.WristSeparationAtImpact(), world)
    assert one_handed.value == pytest.approx(0.6)
    assert two_handed.value == pytest.approx(0.08)


# --- Saque ---------------------------------------------------------------------------------------------

TROPHY = 45


def serve_world() -> FloatArray:
    """Trofeo en el fotograma 45 (brazo de lanzamiento en lo más alto), impacto con el brazo estirado."""
    world = standing_world()
    t = np.arange(world.shape[0], dtype=float)
    toss_y = -0.2 - 1.0 * np.exp(-0.5 * ((t - TROPHY) / 8.0) ** 2)  # pico -1.2 en el trofeo
    world[:, LEFT.wrist] = np.stack([np.full_like(t, -0.2), toss_y, np.zeros_like(t)], axis=-1)
    # Trofeo: hombro de golpeo 0.15 m más bajo y codo a 90°.
    world[TROPHY, LEFT.shoulder] = [-0.2, -0.6, 0.0]
    world[TROPHY, RIGHT.shoulder] = [0.2, -0.45, 0.0]
    world[TROPHY, RIGHT.elbow] = [0.45, -0.45, 0.0]
    world[TROPHY, RIGHT.wrist] = [0.45, -0.7, 0.0]
    # Impacto: brazo vertical y estirado sobre el hombro.
    world[IMPACT, RIGHT.elbow] = [0.2, -0.8, 0.0]
    world[IMPACT, RIGHT.wrist] = [0.2, -1.1, 0.0]
    return world


def test_trophy_frame_is_the_highest_point_of_the_toss_arm() -> None:
    ctx = MetricContext(pose_from_world(serve_world()), SEGMENT, "right")
    assert ctx.trophy_frame == TROPHY


@pytest.mark.parametrize(("metric", "expected"), [
    (m.TrophyElbowAngle(), 90.0),
    (m.TossArmHeight(), 0.45),  # muñeca -1.2 sobre la nariz -0.75
    (m.ShoulderTiltAtTrophy(), float(np.degrees(np.arctan2(0.15, 0.4)))),
    (m.ContactHeight(), 0.35),  # muñeca -1.1 sobre la nariz -0.75
    (m.ElbowAngleAtImpact(), 180.0),
])
def test_serve_metrics_match_constructed_geometry(metric: Metric, expected: float) -> None:
    assert compute(metric, serve_world()).value == pytest.approx(expected, abs=0.01)


def test_shoulder_tilt_is_negative_when_hitting_shoulder_is_higher() -> None:
    world = serve_world()
    world[TROPHY, [LEFT.shoulder, RIGHT.shoulder], 1] = [-0.45, -0.6]
    assert compute(m.ShoulderTiltAtTrophy(), world).value < 0


def test_left_handed_serve_uses_the_other_arms() -> None:
    world = serve_world()
    pairs = [(LEFT.shoulder, RIGHT.shoulder), (LEFT.elbow, RIGHT.elbow), (LEFT.wrist, RIGHT.wrist)]
    for a, b in pairs:
        world[:, [a, b]] = world[:, [b, a]]
    ctx = MetricContext(pose_from_world(world), SEGMENT, "left")
    assert ctx.trophy_frame == TROPHY
    assert m.TrophyElbowAngle().compute(ctx).value == pytest.approx(90.0, abs=0.01)
    assert m.ContactHeight().compute(ctx).value == pytest.approx(0.35, abs=0.01)


# --- Volea ---------------------------------------------------------------------------------------------

def volley_world(backswing: float, follow_through: float) -> FloatArray:
    """La muñeca avanza en línea recta: `backswing` m en los 0.5 s previos y `follow_through` en los 0.3 s
    posteriores al impacto."""
    world = standing_world()
    t = np.arange(world.shape[0], dtype=float)
    before, after = IMPACT - round(0.5 * FPS), IMPACT + round(0.3 * FPS)
    z = np.interp(t, [0, before, IMPACT, after, 139], [-backswing, -backswing, 0.0, follow_through, follow_through])
    world[:, RIGHT.wrist] = np.stack([np.full_like(t, 0.3), np.zeros_like(t), z], axis=-1)
    return world


def test_volley_swing_lengths() -> None:
    compact, long_swing = volley_world(0.3, 0.2), volley_world(0.9, 0.8)
    assert compute(m.BackswingWristTravel(), compact).value == pytest.approx(0.3)
    assert compute(m.FollowThroughLength(), compact).value == pytest.approx(0.2)
    assert compute(m.BackswingWristTravel(), long_swing).value == pytest.approx(0.9)
    assert compute(m.FollowThroughLength(), long_swing).value == pytest.approx(0.8)


def test_windows_never_leave_the_stroke_segment() -> None:
    short = SEGMENT.model_copy(update={"start_frame": IMPACT - 5, "backswing_frame": IMPACT - 5,
                                       "end_frame": IMPACT + 3})
    world = volley_world(0.9, 0.8)
    assert compute(m.BackswingWristTravel(), world, short).value < 0.9
    assert compute(m.FollowThroughLength(), world, short).value < 0.8


def test_hidden_nose_lowers_head_confidence() -> None:
    pose = make_pose()
    visibility = pose.visibility.copy()
    visibility[:, Landmark.NOSE] = 0.0
    pose = replace(pose, visibility=visibility)
    segment = WristSpeedSegmenter().segment(pose, "right")
    ctx = MetricContext(pose, segment, "right")
    assert m.HeadStability().compute(ctx).confidence == pytest.approx(0.9 * 4 / 5)  # nariz oculta, torso visible
