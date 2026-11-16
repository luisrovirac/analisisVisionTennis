"""Relleno de huecos: interpola linealmente los fotogramas sin detección entre detecciones válidas."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from numpy.typing import NDArray

from tennis_worker.pipeline.types import FloatArray, PoseSequence


def interpolate_gaps(pose: PoseSequence) -> PoseSequence:
    """Los fotogramas interpolados conservan detected=False y visibilidad 0 (no aportan confianza)."""
    if pose.detected.all() or not pose.detected.any():
        return pose
    known = np.flatnonzero(pose.detected)
    return replace(pose, image=_interpolate(pose.image, known), world=_interpolate(pose.world, known))


def _interpolate(values: FloatArray, known: NDArray[np.intp]) -> FloatArray:
    frames = np.arange(values.shape[0])
    flat = values.reshape(values.shape[0], -1)
    filled = np.column_stack([np.interp(frames, known, flat[known, col]) for col in range(flat.shape[1])])
    return filled.reshape(values.shape)
