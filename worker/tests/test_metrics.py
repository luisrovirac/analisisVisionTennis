from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from tennis_worker.contracts import MetricValue
from tennis_worker.errors import UnsupportedStrokeError
from tennis_worker.pipeline.biomechanics.base import MetricContext
from tennis_worker.pipeline.biomechanics.registry import default_registry
from tennis_worker.pipeline.landmarks import RIGHT
from tennis_worker.pipeline.segmentation import WristSpeedSegmenter
from tests.synthetic import BAD_SWING, GOOD_SWING, SwingSpec, make_pose


def compute(spec: SwingSpec) -> dict[str, MetricValue]:
    pose = make_pose(spec)
    segment = WristSpeedSegmenter().segment(pose, "right")
    return default_registry().compute_all("forehand", MetricContext(pose, segment, "right"))


@pytest.fixture(scope="module")
def good() -> dict[str, MetricValue]:
    return compute(GOOD_SWING)


@pytest.fixture(scope="module")
def bad() -> dict[str, MetricValue]:
    return compute(BAD_SWING)


def test_all_forehand_metrics_are_computed(good: dict[str, MetricValue]) -> None:
    assert set(good) == default_registry().metric_ids("forehand")
    assert all(m.confidence == pytest.approx(0.9) for m in good.values())


def test_joint_angles_match_synthetic_geometry(good: dict[str, MetricValue]) -> None:
    assert good["elbow_angle_at_impact"].value == pytest.approx(GOOD_SWING.elbow_angle_deg, abs=0.5)
    assert good["knee_angle_min"].value == pytest.approx(GOOD_SWING.knee_angle_deg, abs=0.5)


@pytest.mark.parametrize(("metric", "good_min", "bad_max"), [
    ("shoulder_rotation_range", 45, 45),
    ("hip_shoulder_separation_max", 15, 15),
    ("contact_point_ahead", 0.15, 0.15),
    ("follow_through_height", 0.0, 0.0),
])
def test_good_and_bad_swings_land_on_opposite_sides(
    good: dict[str, MetricValue], bad: dict[str, MetricValue], metric: str, good_min: float, bad_max: float,
) -> None:
    assert good[metric].value > good_min
    assert bad[metric].value < bad_max


def test_kinetic_chain_order(good: dict[str, MetricValue], bad: dict[str, MetricValue]) -> None:
    assert good["kinetic_chain_sequence"].value == 1.0
    assert bad["kinetic_chain_sequence"].value == 0.0


def test_confidence_drops_when_landmarks_are_hidden() -> None:
    pose = make_pose()
    visibility = pose.visibility.copy()
    visibility[:, RIGHT.elbow] = 0.0
    pose = replace(pose, visibility=visibility)
    segment = WristSpeedSegmenter().segment(pose, "right")
    metrics = default_registry().compute_all("forehand", MetricContext(pose, segment, "right"))
    assert metrics["elbow_angle_at_impact"].confidence == pytest.approx(0.6)
    assert metrics["knee_angle_min"].confidence == pytest.approx(0.9)


def test_left_handed_uses_left_arm() -> None:
    pose = make_pose()
    mirrored_world = pose.world.copy()
    mirrored_world[:, [15, 16]] = mirrored_world[:, [16, 15]]  # muñecas
    mirrored_world[:, [13, 14]] = mirrored_world[:, [14, 13]]  # codos
    mirrored_world[:, [11, 12]] = mirrored_world[:, [12, 11]]  # hombros
    mirrored = replace(pose, world=mirrored_world)
    segment = WristSpeedSegmenter().segment(mirrored, "left")
    metrics = default_registry().compute_all("forehand", MetricContext(mirrored, segment, "left"))
    assert metrics["elbow_angle_at_impact"].value == pytest.approx(GOOD_SWING.elbow_angle_deg, abs=0.5)
    assert np.isfinite(metrics["contact_point_ahead"].value)


def test_unsupported_stroke_raises() -> None:
    pose = make_pose()
    segment = WristSpeedSegmenter().segment(pose, "right")
    with pytest.raises(UnsupportedStrokeError):
        default_registry().compute_all("serve", MetricContext(pose, segment, "right"))
