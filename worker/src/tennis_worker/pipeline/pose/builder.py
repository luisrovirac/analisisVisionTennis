"""Acumula detecciones por fotograma y construye un PoseSequence."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from tennis_worker.pipeline.types import LANDMARK_COUNT, FloatArray, PoseSequence

_MISSING_POINTS = np.full((LANDMARK_COUNT, 3), np.nan)
_MISSING_VISIBILITY = np.zeros(LANDMARK_COUNT)


class PoseSequenceBuilder:
    def __init__(self, fps: float) -> None:
        self._fps = fps
        self._image: list[FloatArray] = []
        self._world: list[FloatArray] = []
        self._visibility: list[FloatArray] = []
        self._detected: list[bool] = []

    def add_detection(self, image: Sequence[Sequence[float]], world: Sequence[Sequence[float]],
                      visibility: Sequence[float]) -> None:
        self._append(np.asarray(image, float), np.asarray(world, float), np.asarray(visibility, float), True)

    def add_missing(self) -> None:
        self._append(_MISSING_POINTS, _MISSING_POINTS, _MISSING_VISIBILITY, False)

    def _append(self, image: FloatArray, world: FloatArray, visibility: FloatArray, detected: bool) -> None:
        self._image.append(image)
        self._world.append(world)
        self._visibility.append(visibility)
        self._detected.append(detected)

    def build(self) -> PoseSequence:
        empty = (0, LANDMARK_COUNT)
        return PoseSequence(
            fps=self._fps,
            image=np.stack(self._image) if self._image else np.empty((*empty, 3)),
            world=np.stack(self._world) if self._world else np.empty((*empty, 3)),
            visibility=np.stack(self._visibility) if self._visibility else np.empty(empty),
            detected=np.asarray(self._detected, dtype=bool),
        )
