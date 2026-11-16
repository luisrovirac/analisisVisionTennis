"""Estructuras de datos internas del pipeline."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]

LANDMARK_COUNT = 33


@dataclass(frozen=True)
class PoseSequence:
    """Pose por fotograma.

    image: (T, 33, 3) coordenadas normalizadas a la imagen.
    world: (T, 33, 3) metros aproximados, centrados en la cadera; eje y hacia abajo.
    visibility: (T, 33) en [0, 1].
    detected: (T,) False en fotogramas sin detección (los valores pueden ser NaN o interpolados).
    """

    fps: float
    image: FloatArray
    world: FloatArray
    visibility: FloatArray
    detected: BoolArray

    def __post_init__(self) -> None:
        frames = self.detected.shape[0]
        expected = {
            "image": (frames, LANDMARK_COUNT, 3),
            "world": (frames, LANDMARK_COUNT, 3),
            "visibility": (frames, LANDMARK_COUNT),
        }
        for name, shape in expected.items():
            if getattr(self, name).shape != shape:
                raise ValueError(f"{name} tiene forma {getattr(self, name).shape}, se esperaba {shape}")
        if self.fps <= 0:
            raise ValueError("fps debe ser positivo")

    @property
    def frame_count(self) -> int:
        return int(self.detected.shape[0])

    @property
    def detection_rate(self) -> float:
        return float(self.detected.mean()) if self.frame_count else 0.0
