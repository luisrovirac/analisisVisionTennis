"""Características de la secuencia de pose para el clasificador de golpes.

Este módulo es la ÚNICA implementación: el entrenamiento (ml/) lo importa tal cual. Así no hay
diferencias entre entrenamiento e inferencia. Si cambia el cálculo, hay que subir FEATURE_SET:
el worker se niega a cargar un modelo entrenado con otra versión (meta.json → feature_set).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from tennis_worker.contracts import Handedness
from tennis_worker.pipeline.landmarks import Landmark
from tennis_worker.pipeline.types import LANDMARK_COUNT, PoseSequence

FEATURE_SET = "world_xyz_torso_v1"
FEATURES_PER_FRAME = LANDMARK_COUNT * 3

# Pares izquierda/derecha de MediaPipe Pose: al espejar un zurdo se intercambian.
_MIRROR_PAIRS = [(1, 4), (2, 5), (3, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16), (17, 18),
                 (19, 20), (21, 22), (23, 24), (25, 26), (27, 28), (29, 30), (31, 32)]
_MIRROR_ORDER = np.arange(LANDMARK_COUNT)
for _a, _b in _MIRROR_PAIRS:
    _MIRROR_ORDER[_a], _MIRROR_ORDER[_b] = _b, _a

Float32Array = NDArray[np.float32]


def sequence_features(pose: PoseSequence, handedness: Handedness, seq_len: int) -> Float32Array:
    """(seq_len, 99): coordenadas world de los 33 puntos, en unidades de torso y remuestreadas.

    - Escala por la longitud del torso: independiente de la estatura y la distancia a la cámara.
    - Los zurdos se espejan (x → -x e intercambio izquierda/derecha): el modelo solo ve diestros.
    - Remuestreo temporal a seq_len: independiente de los fps y de la duración del clip.
    """
    if pose.frame_count == 0:
        return np.zeros((seq_len, FEATURES_PER_FRAME), dtype=np.float32)
    world = np.nan_to_num(pose.world.astype(np.float64))
    if handedness == "left":
        world = world[:, _MIRROR_ORDER, :] * np.array([-1.0, 1.0, 1.0])
    world = world / _torso_length(world)
    flat = world.reshape(pose.frame_count, FEATURES_PER_FRAME)
    return _resample(flat, seq_len).astype(np.float32)


def _torso_length(world: NDArray[np.float64]) -> float:
    shoulders = world[:, [Landmark.LEFT_SHOULDER, Landmark.RIGHT_SHOULDER], :].mean(axis=1)
    hips = world[:, [Landmark.LEFT_HIP, Landmark.RIGHT_HIP], :].mean(axis=1)
    length = float(np.median(np.linalg.norm(shoulders - hips, axis=1)))
    return length if length > 1e-6 else 1.0


def _resample(flat: NDArray[np.float64], seq_len: int) -> NDArray[np.float64]:
    frames = flat.shape[0]
    source = np.linspace(0.0, 1.0, frames)
    target = np.linspace(0.0, 1.0, seq_len)
    return np.stack([np.interp(target, source, flat[:, j]) for j in range(flat.shape[1])], axis=1)
