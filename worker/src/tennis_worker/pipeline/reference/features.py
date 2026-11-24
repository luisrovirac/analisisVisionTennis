"""Características por fotograma para comparar golpes: ángulos articulares, invariantes a la escala,
a la posición del jugador y a la orientación de la cámara (los giros se miden respecto al impacto)."""

from __future__ import annotations

import numpy as np

from tennis_worker.contracts import Handedness
from tennis_worker.pipeline import geometry
from tennis_worker.pipeline.landmarks import LEFT, RIGHT, body_sides
from tennis_worker.pipeline.types import FloatArray, PoseSequence

FEATURE_NAMES = (
    "codo dominante", "hombro dominante", "codo no dominante", "rodilla dominante", "rodilla no dominante",
    "giro de hombros", "giro de caderas", "separación cadera-hombro", "inclinación del tronco",
)
# Landmarks en los que se apoyan las características: su visibilidad decide si una fase se puntúa.
FEATURE_LANDMARKS = (LEFT.shoulder, RIGHT.shoulder, LEFT.elbow, RIGHT.elbow, LEFT.wrist, RIGHT.wrist,
                     LEFT.hip, RIGHT.hip, LEFT.knee, RIGHT.knee, LEFT.ankle, RIGHT.ankle)
_RESAMPLE_HZ = 30.0  # base temporal común: referencias a 120 fps y usuarios a 30 fps se comparan igual


def frame_features(pose: PoseSequence, handedness: Handedness, impact_frame: int) -> FloatArray:
    """(T, F) en grados. Lado dominante/no dominante en lugar de izquierdo/derecho, y los giros de un
    zurdo se reflejan: así una referencia diestra sirve para un zurdo."""
    w = pose.world
    dom, non = body_sides(handedness).dominant, body_sides(handedness).non_dominant
    shoulder_yaw = geometry.horizontal_yaw(w[:, LEFT.shoulder], w[:, RIGHT.shoulder])
    hip_yaw = geometry.horizontal_yaw(w[:, LEFT.hip], w[:, RIGHT.hip])
    mirror = -1.0 if handedness == "left" else 1.0
    shoulder_turn = mirror * geometry.wrap_degrees(shoulder_yaw - shoulder_yaw[impact_frame])
    hip_turn = mirror * geometry.wrap_degrees(hip_yaw - hip_yaw[impact_frame])
    separation = mirror * geometry.wrap_degrees(shoulder_yaw - hip_yaw)
    mid_hip = (w[:, LEFT.hip] + w[:, RIGHT.hip]) / 2
    mid_shoulder = (w[:, LEFT.shoulder] + w[:, RIGHT.shoulder]) / 2
    up = mid_hip + np.array([0.0, -1.0, 0.0])  # y hacia abajo: "arriba" es -y
    trunk_tilt = geometry.joint_angle(mid_shoulder, mid_hip, up)
    columns = (
        geometry.joint_angle(w[:, dom.shoulder], w[:, dom.elbow], w[:, dom.wrist]),
        geometry.joint_angle(w[:, dom.hip], w[:, dom.shoulder], w[:, dom.elbow]),
        geometry.joint_angle(w[:, non.shoulder], w[:, non.elbow], w[:, non.wrist]),
        geometry.joint_angle(w[:, dom.hip], w[:, dom.knee], w[:, dom.ankle]),
        geometry.joint_angle(w[:, non.hip], w[:, non.knee], w[:, non.ankle]),
        shoulder_turn, hip_turn, separation, trunk_tilt,
    )
    return np.column_stack(columns).astype(np.float64)


def resample(features: FloatArray, fps: float) -> FloatArray:
    """Interpola linealmente a _RESAMPLE_HZ, conservando la duración real de la fase."""
    n = len(features)
    if n < 2:
        return features
    duration = (n - 1) / fps
    samples = max(2, round(duration * _RESAMPLE_HZ) + 1)
    source = np.linspace(0.0, duration, n)
    target = np.linspace(0.0, duration, samples)
    return np.column_stack([np.interp(target, source, features[:, k]) for k in range(features.shape[1])])
