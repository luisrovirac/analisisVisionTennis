"""Robustez de la medición: suavizado Savitzky-Golay y prueba de sensibilidad al fotograma de impacto."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from tennis_worker.contracts import MetricValue
from tennis_worker.pipeline.biomechanics.base import MetricContext, measured
from tennis_worker.pipeline.biomechanics.registry import MetricRegistry, default_registry
from tennis_worker.pipeline.biomechanics.robustness import TemporalRobustness, stability
from tennis_worker.pipeline.pose.postprocess import savgol_coefficients, smooth_pose
from tennis_worker.pipeline.segmentation import WristSpeedSegmenter
from tests.synthetic import GOOD_SWING, make_pose

# --- Suavizado -----------------------------------------------------------------------------------------


def test_savgol_preserves_polynomials_up_to_its_order() -> None:
    kernel = savgol_coefficients(7, 2)
    assert kernel.sum() == pytest.approx(1.0)
    x = np.arange(-3, 4, dtype=float)
    assert kernel @ (2 * x**2 - x + 5) == pytest.approx(5.0)  # valor central de la parábola


def test_savgol_rejects_invalid_windows() -> None:
    with pytest.raises(ValueError):
        savgol_coefficients(6, 2)


def test_smoothing_removes_jitter_but_keeps_the_motion() -> None:
    pose = make_pose()
    noisy_world = pose.world + np.random.default_rng(3).normal(0, 0.01, pose.world.shape)
    noisy = replace(pose, world=noisy_world, image=noisy_world.copy())
    smoothed = smooth_pose(noisy, window_seconds=0.1)
    error_before = np.abs(noisy.world - pose.world).mean()
    error_after = np.abs(smoothed.world - pose.world).mean()
    assert error_after < 0.7 * error_before
    assert smoothed.world.shape == pose.world.shape
    assert np.array_equal(smoothed.visibility, pose.visibility)


@pytest.mark.parametrize("seconds", [0.0, -1.0])
def test_smoothing_can_be_disabled(seconds: float) -> None:
    pose = make_pose()
    assert smooth_pose(pose, seconds) is pose


def test_smoothing_skips_clips_shorter_than_the_window() -> None:
    pose = make_pose(replace(GOOD_SWING, frames=5, impact=2, hip_peak=1, shoulder_peak=1))
    assert smooth_pose(pose, 0.5) is pose


# --- Sensibilidad al fotograma de impacto ----------------------------------------------------------------


class ImpactFrameNumber:
    """Métrica de prueba: devuelve el número del fotograma de impacto × escala (máximamente inestable)."""

    id, label, unit = "impact_frame_number", "Fotograma de impacto", "deg"

    def __init__(self, scale: float) -> None:
        self.scale = scale

    def compute(self, ctx: MetricContext) -> MetricValue:
        return measured(self, ctx.segment.impact_frame * self.scale, 0.9)


def robust_value(scale: float) -> MetricValue:
    pose = make_pose()
    segment = WristSpeedSegmenter().segment(pose, "right")
    registry = MetricRegistry({"forehand": [ImpactFrameNumber(scale)]})
    return TemporalRobustness(registry).compute_all("forehand", MetricContext(pose, segment, "right"))[
        "impact_frame_number"]


def test_stability_formula() -> None:
    assert stability(0.0, 10.0) == 1.0
    assert stability(10.0, 10.0) == 0.5
    assert stability(30.0, 10.0) == pytest.approx(0.1)


def test_stable_metric_keeps_its_confidence() -> None:
    value = robust_value(scale=0.1)  # 60 fps × 0,05 s = ±3 fotogramas → varía 0,6° (tolerancia de 15°)
    assert value.value == pytest.approx(7.0)  # mediana de ±2 = valor central
    assert value.confidence == pytest.approx(0.9 * stability(0.6, 15.0), abs=1e-3)
    assert value.confidence > 0.89


def test_unstable_metric_loses_its_confidence() -> None:
    value = robust_value(scale=6.0)  # varía 36° en ±3 fotogramas
    assert value.confidence < 0.2


def test_audio_confirmed_impact_uses_a_narrower_window() -> None:
    pose = make_pose()
    segment = WristSpeedSegmenter().segment(pose, "right")
    robust = TemporalRobustness(MetricRegistry({"forehand": [ImpactFrameNumber(6.0)]}))
    wide = MetricContext(pose, segment, "right")
    narrow = MetricContext(pose, segment, "right", impact_uncertainty_s=1 / 60)
    assert (robust.window_frames(wide), robust.window_frames(narrow)) == (3, 1)
    narrow_conf = robust.compute_all("forehand", narrow)["impact_frame_number"].confidence
    wide_conf = robust.compute_all("forehand", wide)["impact_frame_number"].confidence
    assert narrow_conf > wide_conf


def test_window_is_at_least_one_frame() -> None:
    segment = WristSpeedSegmenter().segment(make_pose(), "right")
    slow = replace(make_pose(), fps=10.0)
    assert TemporalRobustness(default_registry()).window_frames(MetricContext(slow, segment, "right")) == 1


def test_robust_registry_keeps_all_metrics_and_their_metadata() -> None:
    pose = make_pose()
    segment = WristSpeedSegmenter().segment(pose, "right")
    ctx = MetricContext(pose, segment, "right")
    plain = default_registry().compute_all("forehand", ctx)
    robust = TemporalRobustness(default_registry()).compute_all("forehand", ctx)
    assert set(robust) == set(plain)
    for metric_id, value in robust.items():
        assert (value.label, value.unit) == (plain[metric_id].label, plain[metric_id].unit)
        assert value.confidence <= plain[metric_id].confidence


def test_sensitivity_can_be_disabled() -> None:
    pose = make_pose()
    segment = WristSpeedSegmenter().segment(pose, "right")
    ctx = MetricContext(pose, segment, "right")
    robust = TemporalRobustness(default_registry(), enabled=False)
    assert robust.compute_all("forehand", ctx) == default_registry().compute_all("forehand", ctx)


def test_invalid_window_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        TemporalRobustness(default_registry(), default_uncertainty_s=0)


def test_low_fps_still_gets_a_real_smoothing_window() -> None:
    pose = replace(make_pose(), fps=30.0)
    noisy_world = pose.world + np.random.default_rng(5).normal(0, 0.01, pose.world.shape)
    smoothed = smooth_pose(replace(pose, world=noisy_world), window_seconds=0.1)  # 3 fotogramas → mínimo 5
    assert not np.allclose(smoothed.world, noisy_world)
