"""Dynamic Time Warping con banda de Sakoe-Chiba, en numpy (sin dependencias nuevas)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from tennis_worker.pipeline.types import FloatArray


@dataclass(frozen=True)
class DtwResult:
    cost: float  # coste medio por paso del camino de alineación (unidades de las características)
    path: tuple[tuple[int, int], ...]  # pares (i, j) alineados, del inicio al final


def dtw(a: FloatArray, b: FloatArray, band: float = 0.3) -> DtwResult:
    """Alinea dos secuencias (n, F) y (m, F) permitiendo estirarlas o comprimirlas en el tiempo.

    El coste local es la media de |a_i - b_j| entre características. La banda limita cuánto puede
    desviarse el camino de la diagonal, en tiempo normalizado (|i/n - j/m| <= band): evita alineaciones
    absurdas (toda la preparación contra un solo fotograma) y acota el cálculo. El coste total se divide
    por la longitud del camino, para comparar fases de duración distinta."""
    a, b = np.atleast_2d(a), np.atleast_2d(b)
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        raise ValueError("las secuencias no pueden estar vacías")
    if not (np.isfinite(a).all() and np.isfinite(b).all()):
        raise ValueError("las secuencias contienen NaN o infinitos")
    local = np.abs(a[:, None, :] - b[None, :, :]).mean(axis=2)
    ti = np.arange(n)[:, None] / max(n - 1, 1)
    tj = np.arange(m)[None, :] / max(m - 1, 1)
    # La banda nunca puede excluir la diagonal (ni los extremos) aunque las longitudes difieran.
    allowed = np.abs(ti - tj) <= max(band, 1.0 / min(n, m))
    acc = np.full((n + 1, m + 1), np.inf)
    acc[0, 0] = 0.0
    steps = np.zeros((n + 1, m + 1), dtype=np.int64)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if not allowed[i - 1, j - 1]:
                continue
            options = (acc[i - 1, j - 1], acc[i - 1, j], acc[i, j - 1])
            best = int(np.argmin(options))
            acc[i, j] = local[i - 1, j - 1] + options[best]
            steps[i, j] = best
    if not np.isfinite(acc[n, m]):
        raise ValueError("la banda no permite ningún camino de alineación")
    path = _backtrack(steps, n, m)
    return DtwResult(cost=float(acc[n, m] / len(path)), path=path)


def _backtrack(steps: NDArray[np.int64], n: int, m: int) -> tuple[tuple[int, int], ...]:
    i, j = n, m
    path = []
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        move = steps[i, j]
        if move == 0:
            i, j = i - 1, j - 1
        elif move == 1:
            i -= 1
        else:
            j -= 1
    return tuple(reversed(path))
