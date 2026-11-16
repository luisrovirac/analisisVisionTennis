from __future__ import annotations

import numpy as np
import pytest

from tennis_worker.pipeline import geometry


def test_joint_angle_right_and_straight() -> None:
    b = np.array([0.0, 0.0, 0.0])
    assert geometry.joint_angle(np.array([1.0, 0, 0]), b, np.array([0, 1.0, 0])) == pytest.approx(90)
    assert geometry.joint_angle(np.array([1.0, 0, 0]), b, np.array([-1.0, 0, 0])) == pytest.approx(180)


def test_horizontal_yaw_is_continuous_across_180() -> None:
    angles = np.radians(np.array([170.0, 179.0, 189.0, 200.0]))
    right = np.stack([np.cos(angles), np.zeros(4), np.sin(angles)], axis=-1)
    yaw = geometry.horizontal_yaw(np.zeros((4, 3)), right)
    np.testing.assert_allclose(yaw, [170, 179, 189, 200], atol=1e-6)


def test_wrap_degrees() -> None:
    np.testing.assert_allclose(geometry.wrap_degrees(np.array([190.0, -190.0, 30.0])), [-170, 170, 30])


def test_smooth_preserves_length_and_constant() -> None:
    x = np.full(10, 3.0)
    assert geometry.smooth(x, 4).shape == (10,)
    np.testing.assert_allclose(geometry.smooth(x, 4), 3.0)
    assert geometry.smooth(x, 1) is x


def test_speed_and_angular_speed() -> None:
    points = np.stack([np.arange(5.0), np.zeros(5), np.zeros(5)], axis=-1)  # 1 unidad por fotograma
    np.testing.assert_allclose(geometry.speed(points, fps=30), 30)
    np.testing.assert_allclose(geometry.angular_speed(-np.arange(5.0) * 2, fps=10), 20)
