"""Generador de swings sintéticos con geometría conocida para probar métricas y segmentación.

Modelo: cadera centrada en el origen (y hacia abajo, como MediaPipe world). Hombros y caderas giran
alrededor del eje vertical con perfiles sigmoides cuyo pico de velocidad se controla por fotograma.
La muñeca dominante (derecha) describe un arco horizontal con pico de velocidad en `impact`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tennis_worker.pipeline.landmarks import LEFT, RIGHT
from tennis_worker.pipeline.types import LANDMARK_COUNT, FloatArray, PoseSequence


@dataclass(frozen=True)
class SwingSpec:
    fps: float = 60.0
    frames: int = 140
    impact: int = 70
    hip_peak: int = 60
    shoulder_peak: int = 64
    shoulder_start_deg: float = -100.0
    hip_start_deg: float = -60.0
    wrist_start_deg: float = -150.0
    wrist_end_deg: float = 60.0
    elbow_angle_deg: float = 140.0
    knee_angle_deg: float = 140.0
    follow_through_wrist_y: float = -0.8  # y hacia abajo: -0.8 está por encima del hombro (-0.5)
    visibility: float = 0.9


GOOD_SWING = SwingSpec()
BAD_SWING = SwingSpec(
    shoulder_start_deg=-25.0, hip_start_deg=-25.0,  # sin giro y en bloque
    hip_peak=66, shoulder_peak=58,  # hombros antes que cadera
    wrist_start_deg=-60.0, wrist_end_deg=-10.0,  # arco corto → impacto atrasado
    elbow_angle_deg=90.0, knee_angle_deg=175.0, follow_through_wrist_y=-0.2,
)


def _sigmoid(t: FloatArray, center: float, tau: float) -> FloatArray:
    return 1.0 / (1.0 + np.exp(-(t - center) / tau))


def _ring(center: FloatArray, yaw_deg: FloatArray, half_width: float) -> tuple[FloatArray, FloatArray]:
    rad = np.radians(yaw_deg)
    offset = np.stack([np.cos(rad), np.zeros_like(rad), np.sin(rad)], axis=-1) * half_width
    return center - offset, center + offset


def _bent_joint(a: FloatArray, c: FloatArray, angle_deg: float, axis: FloatArray) -> FloatArray:
    """Punto B tal que el ángulo ABC = angle_deg, con |AB| = |BC|, desplazado perpendicularmente a AC."""
    ac = c - a
    d = np.linalg.norm(ac, axis=-1, keepdims=True)
    half = np.radians(angle_deg) / 2
    length = d / (2 * np.sin(half))
    perp = np.cross(ac, axis)
    perp /= np.linalg.norm(perp, axis=-1, keepdims=True)
    return (a + c) / 2 + perp * length * np.cos(half)


def make_pose(spec: SwingSpec = GOOD_SWING) -> PoseSequence:
    t = np.arange(spec.frames, dtype=float)
    world = np.zeros((spec.frames, LANDMARK_COUNT, 3))
    shoulder_yaw = spec.shoulder_start_deg * (1 - _sigmoid(t, spec.shoulder_peak, 4.0))
    hip_yaw = spec.hip_start_deg * (1 - _sigmoid(t, spec.hip_peak, 4.0))
    shoulder_center = np.tile([0.0, -0.5, 0.0], (spec.frames, 1))
    world[:, LEFT.shoulder], world[:, RIGHT.shoulder] = _ring(shoulder_center, shoulder_yaw, 0.2)
    world[:, LEFT.hip], world[:, RIGHT.hip] = _ring(np.zeros((spec.frames, 3)), hip_yaw, 0.15)

    sweep = _sigmoid(t, spec.impact, 4.0)
    wrist_deg = spec.wrist_start_deg + (spec.wrist_end_deg - spec.wrist_start_deg) * sweep
    wrist_y = -0.3 + (spec.follow_through_wrist_y + 0.3) * _sigmoid(t, spec.impact + 12, 4.0)
    rad = np.radians(wrist_deg)
    world[:, RIGHT.wrist] = np.stack([0.6 * np.cos(rad), wrist_y, 0.6 * np.sin(rad)], axis=-1)
    world[:, RIGHT.elbow] = _bent_joint(world[:, RIGHT.shoulder], world[:, RIGHT.wrist],
                                        spec.elbow_angle_deg, np.array([0.0, 1.0, 0.0]))
    world[:, LEFT.wrist] = world[:, LEFT.shoulder] + [0.0, 0.4, 0.1]
    world[:, LEFT.elbow] = world[:, LEFT.shoulder] + [0.0, 0.2, 0.05]
    _add_legs(world, spec)
    return PoseSequence(
        fps=spec.fps, image=world.copy(), world=world,
        visibility=np.full((spec.frames, LANDMARK_COUNT), spec.visibility),
        detected=np.ones(spec.frames, dtype=bool),
    )


def _add_legs(world: FloatArray, spec: SwingSpec) -> None:
    for side in (LEFT, RIGHT):
        world[:, side.ankle] = world[:, side.hip] + [0.0, 0.9, 0.0]
        world[:, side.knee] = _bent_joint(world[:, side.hip], world[:, side.ankle],
                                          spec.knee_angle_deg, np.array([1.0, 0.0, 0.0]))


def static_pose(frames: int = 60) -> PoseSequence:
    """Jugador quieto: sin golpe."""
    pose = make_pose(SwingSpec(frames=frames))
    still = np.repeat(pose.world[:1], frames, axis=0)
    return PoseSequence(fps=pose.fps, image=still, world=still, visibility=pose.visibility,
                        detected=pose.detected)
