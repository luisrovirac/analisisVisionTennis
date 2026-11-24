"""Posproceso de la pose: relleno de huecos y suavizado temporal."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from numpy.typing import NDArray

from tennis_worker.pipeline.types import FloatArray, PoseSequence


def interpolate_gaps(pose: PoseSequence) -> PoseSequence:
    """Interpola linealmente los fotogramas sin detección entre detecciones válidas.
    Los fotogramas interpolados conservan detected=False y visibilidad 0 (no aportan confianza)."""
    if pose.detected.all() or not pose.detected.any():
        return pose
    known = np.flatnonzero(pose.detected)
    return replace(pose, image=_interpolate(pose.image, known), world=_interpolate(pose.world, known))


def _interpolate(values: FloatArray, known: NDArray[np.intp]) -> FloatArray:
    frames = np.arange(values.shape[0])
    flat = values.reshape(values.shape[0], -1)
    filled = np.column_stack([np.interp(frames, known, flat[known, col]) for col in range(flat.shape[1])])
    return filled.reshape(values.shape)


def savgol_coefficients(window: int, polyorder: int) -> FloatArray:
    """Coeficientes del filtro Savitzky-Golay (suavizado del valor central) para una ventana impar."""
    if window % 2 == 0 or window <= polyorder:
        raise ValueError("la ventana debe ser impar y mayor que el orden del polinomio")
    half = window // 2
    offsets = np.arange(-half, half + 1, dtype=float)
    vandermonde = offsets[:, None] ** np.arange(polyorder + 1)[None, :]
    coefficients: FloatArray = np.linalg.pinv(vandermonde)[0]
    return coefficients


def smooth_pose(pose: PoseSequence, window_seconds: float, polyorder: int = 2) -> PoseSequence:
    """Suaviza las trayectorias (image y world) con Savitzky-Golay. Quita el temblor fotograma a fotograma
    del estimador sin aplanar los picos como una media móvil: un polinomio de orden 2 conserva bien la
    forma de una aceleración. window_seconds <= 0 lo desactiva. Ejecutar después de interpolate_gaps."""
    # Impar y con al menos polyorder + 3 puntos: con polyorder + 1 el polinomio pasa por todos y no suaviza
    # (p. ej. 0,1 s a 30 fps serían 3 fotogramas).
    window = max(round(window_seconds * pose.fps) | 1, (polyorder + 3) | 1)
    if window_seconds <= 0 or pose.frame_count < window:
        return pose
    kernel = savgol_coefficients(window, polyorder)
    return replace(pose, image=_filter(pose.image, kernel), world=_filter(pose.world, kernel))


def _filter(values: FloatArray, kernel: FloatArray) -> FloatArray:
    if np.isnan(values).any():  # sin ninguna detección no hay nada que suavizar
        return values
    half = len(kernel) // 2
    padded = np.pad(values, ((half, half), (0, 0), (0, 0)), mode="edge")
    flat = padded.reshape(padded.shape[0], -1)
    # Kernel simétrico: la correlación coincide con la convolución.
    smoothed = np.column_stack([np.convolve(flat[:, col], kernel[::-1], mode="valid") for col in range(flat.shape[1])])
    result: FloatArray = smoothed.reshape(values.shape)
    return result
