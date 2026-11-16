"""Funciones geométricas vectorizadas sobre series temporales de puntos 3D."""

from __future__ import annotations

import numpy as np

from tennis_worker.pipeline.types import FloatArray


def joint_angle(a: FloatArray, b: FloatArray, c: FloatArray) -> FloatArray:
    """Ángulo ABC en grados (180 = segmento recto). Acepta (..., 3)."""
    ba, bc = a - b, c - b
    # np.maximum (no "+ eps") para no sesgar el coseno: arccos amplifica el error cerca de 180°.
    norms = np.maximum(np.linalg.norm(ba, axis=-1) * np.linalg.norm(bc, axis=-1), 1e-12)
    cos = np.sum(ba * bc, axis=-1) / norms
    angle: FloatArray = np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))
    return angle


def horizontal_yaw(left: FloatArray, right: FloatArray) -> FloatArray:
    """Orientación (grados, sin saltos de ±180) de la línea izquierda→derecha en el plano horizontal x-z."""
    d = right - left
    return np.degrees(np.unwrap(np.arctan2(d[..., 2], d[..., 0])))


def wrap_degrees(x: FloatArray) -> FloatArray:
    return (x + 180.0) % 360.0 - 180.0


def smooth(x: FloatArray, window: int) -> FloatArray:
    """Media móvil centrada; conserva la longitud replicando los bordes."""
    if window <= 1:
        return x
    pad = window // 2
    padded = np.pad(x, (pad, window - 1 - pad), mode="edge")
    return np.convolve(padded, np.ones(window) / window, mode="valid")


def speed(points: FloatArray, fps: float) -> FloatArray:
    """Rapidez (unidades/s) de una serie (T, 3)."""
    result: FloatArray = np.linalg.norm(np.gradient(points, axis=0), axis=-1) * fps
    return result


def angular_speed(angles_deg: FloatArray, fps: float) -> FloatArray:
    """Velocidad angular absoluta (grados/s) de una serie (T,)."""
    result: FloatArray = np.abs(np.gradient(angles_deg)) * fps
    return result
