"""Corrección del esqueleto crudo: inversiones izquierda/derecha del cuerpo entero y picos de un fotograma.

Portado de tennis-analyzer (scripts/corregir_esqueleto.py y evaluar_esqueleto.py, licencia MIT,
© 2026 Rubén, https://github.com/rubenG1009/tennis-analyzer), adaptado a `PoseSequence`.

Inversiones. BlazePose duda entre "mira a cámara" y "está de espaldas" y, cuando duda, voltea el cuerpo
entero (hombros, caderas, brazos y piernas cambian de lado a la vez) sin que baje la visibilidad. Como la
inversión es de cuerpo entero, por fotograma hay dos estados, "tal cual" o "volteado", y se elige la
secuencia más continua con Viterbi: la transición cuesta lo que se mueven las articulaciones (en largos de
torso) y cada fotograma volteado paga una penalización fija pequeña. Resultado: la asignación más continua
que menos se aparta de lo que dijo el modelo.

Limitación conocida: sin ancla externa (el original usa la raqueta detectada), la continuidad no sabe cuál
de las dos orientaciones es la buena. Si el modelo se equivoca durante la mayor parte de un tramo continuo,
el tramo queda coherente pero al revés.

Picos. Un punto que se aleja del punto medio de sus dos vecinos más de `spike_threshold` torsos, y es el
máximo local de esa desviación (sus vecinos se desvían la mitad y no deben tocarse), se sustituye por ese
punto medio.

Ninguna corrección cruza un fotograma sin detección ni un salto de persona (la cadera se teletransporta o el
torso cambia de escala): ahí la cadena se corta y cada tramo se resuelve por separado.

La decisión se toma en el plano de la imagen (en píxeles si se conoce `frame_size`); la corrección se aplica
igual a `image`, `world` y `visibility`. Debe ejecutarse sobre la pose cruda, antes de interpolar y suavizar:
un volteo suavizado se convierte en una curva plausible imposible de detectar después.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from tennis_worker.pipeline.landmarks import LEFT, MIRROR_ORDER, RIGHT, Side
from tennis_worker.pipeline.types import BoolArray, FloatArray, PoseSequence

_PARTS = ("shoulder", "elbow", "wrist", "hip", "knee", "ankle")  # la cara no decide: es ruidosa de perfil
_LEFT = [getattr(LEFT, p) for p in _PARTS]
_RIGHT = [getattr(RIGHT, p) for p in _PARTS]


@dataclass(frozen=True)
class SkeletonCorrection:
    pose: PoseSequence
    flipped: BoolArray  # (T,) fotogramas con izquierda/derecha intercambiadas
    spikes: BoolArray  # (T, 33) puntos sustituidos por el punto medio de sus vecinos

    @property
    def changed(self) -> bool:
        return bool(self.flipped.any() or self.spikes.any())


@dataclass(frozen=True)
class SkeletonCorrector:
    flip_penalty: float = 0.1  # torsos por fotograma volteado
    spike_threshold: float = 0.35  # torsos de desviación respecto al punto medio de los vecinos
    jump_hip: float = 0.6  # torsos que se mueve el centro de la cadera en un fotograma
    jump_scale: float = 0.35  # cambio relativo del largo del torso en un fotograma

    def correct(self, pose: PoseSequence) -> SkeletonCorrection:
        none = np.zeros(pose.frame_count, dtype=bool)
        if pose.frame_count < 2:
            return SkeletonCorrection(pose, none, np.zeros((pose.frame_count, pose.image.shape[1]), dtype=bool))
        xy = _pixels(pose)
        torso = torso_length(xy)
        valid = pose.detected & ~np.isnan(torso)
        jumps = self.person_jumps(xy, torso)
        flipped = self.flips(xy, torso, valid, jumps)
        image, world, visibility = (_flip(a, flipped) for a in (pose.image, pose.world, pose.visibility))
        xy = _flip(xy, flipped)
        spikes = self.spikes(xy, torso, ~valid | jumps)
        image, world = _replace_spikes(image, spikes), _replace_spikes(world, spikes)
        if not flipped.any() and not spikes.any():
            return SkeletonCorrection(pose, flipped, spikes)
        return SkeletonCorrection(replace(pose, image=image, world=world, visibility=visibility), flipped, spikes)

    def person_jumps(self, xy: FloatArray, torso: FloatArray) -> BoolArray:
        """(T,) True en el fotograma t si entre t-1 y t la cadera se teletransporta o el torso cambia de escala."""
        hip = _center(xy, LEFT, RIGHT, "hip")
        with np.errstate(invalid="ignore", divide="ignore"):
            moved = np.linalg.norm(np.diff(hip, axis=0), axis=-1) / torso[:-1]
            rescaled = np.abs(np.diff(torso)) / torso[:-1]
            jumps = np.nan_to_num((moved > self.jump_hip) | (rescaled > self.jump_scale), nan=0.0).astype(bool)
        return np.concatenate([[False], jumps])

    def flips(self, xy: FloatArray, torso: FloatArray, valid: BoolArray, jumps: BoolArray) -> BoolArray:
        """Viterbi de dos estados en cada tramo continuo. True = el fotograma debe voltearse."""
        flipped = np.zeros(len(xy), dtype=bool)
        transitions = transition_costs(xy[:, _LEFT], xy[:, _RIGHT], torso)
        for start, end in segments(valid, jumps):
            flipped[start:end] = self._viterbi(transitions[start:end - 1])
        return flipped

    def _viterbi(self, transitions: FloatArray) -> BoolArray:
        """transitions: (n-1, 2, 2) coste de pasar del estado i (fotograma t) al j (t+1). Devuelve (n,)."""
        emission = np.array([0.0, self.flip_penalty])
        cost = emission.copy()
        back = np.zeros((len(transitions), 2), dtype=int)
        for t, step in enumerate(transitions):
            total = cost[:, None] + step
            back[t] = np.argmin(total, axis=0)
            cost = total[back[t], [0, 1]] + emission
        states = np.zeros(len(transitions) + 1, dtype=bool)
        state = int(np.argmin(cost))
        for t in range(len(transitions), 0, -1):
            states[t] = bool(state)
            state = int(back[t - 1, state])
        states[0] = bool(state)
        return states

    def spikes(self, xy: FloatArray, torso: FloatArray, breaks: BoolArray) -> BoolArray:
        """(T, 33) picos de un fotograma; ni el fotograma de un corte ni sus vecinos cuentan."""
        result = np.zeros(xy.shape[:2], dtype=bool)
        if len(xy) < 3:
            return result
        with np.errstate(invalid="ignore"):
            deviation = np.linalg.norm(xy[1:-1] - (xy[:-2] + xy[2:]) / 2, axis=-1) / torso[1:-1, None]
        deviation = np.nan_to_num(deviation, nan=0.0)
        padded = np.pad(deviation, ((1, 1), (0, 0)))
        local_max = (padded[1:-1] >= padded[:-2]) & (padded[1:-1] >= padded[2:])
        result[1:-1] = (deviation > self.spike_threshold) & local_max
        near_break = breaks | np.roll(breaks, -1) | np.roll(breaks, 1)
        result[near_break] = False
        return result


def torso_length(xy: FloatArray) -> FloatArray:
    """(T,) distancia entre el centro de los hombros y el de las caderas; NaN sin detección o si es 0."""
    length = np.linalg.norm(_center(xy, LEFT, RIGHT, "shoulder") - _center(xy, LEFT, RIGHT, "hip"), axis=-1)
    with np.errstate(invalid="ignore"):
        return np.where(length > 0, length, np.nan)


def transition_costs(left: FloatArray, right: FloatArray, torso: FloatArray) -> FloatArray:
    """(T-1, 2, 2): coste[t, i, j] de pasar del estado i en t al estado j en t+1 (0 = tal cual, 1 = volteado),
    como suma de desplazamientos de las k partes de cada lado en torsos. left/right: (T, k, 2)."""
    states = (np.stack([left, right], axis=1), np.stack([right, left], axis=1))  # cada uno (T, 2, k, 2)
    costs = np.empty((len(left) - 1, 2, 2))
    for i in (0, 1):
        for j in (0, 1):
            moved = np.linalg.norm(states[j][1:] - states[i][:-1], axis=-1).sum(axis=(1, 2))
            costs[:, i, j] = moved / torso[1:]
    return np.nan_to_num(costs, nan=0.0)


def segments(valid: BoolArray, jumps: BoolArray) -> list[tuple[int, int]]:
    """Tramos [inicio, fin) de fotogramas válidos consecutivos; un salto de persona en t abre tramo en t."""
    result: list[tuple[int, int]] = []
    start: int | None = None
    for t, ok in enumerate(valid):
        if start is not None and (not ok or jumps[t]):
            result.append((start, t))
            start = None
        if ok and start is None:
            start = t
    if start is not None:
        result.append((start, len(valid)))
    return result


def _pixels(pose: PoseSequence) -> FloatArray:
    xy = pose.image[..., :2].copy()
    if pose.frame_size is not None:
        xy *= np.array(pose.frame_size, dtype=float)
    return xy


def _center(xy: FloatArray, a: Side, b: Side, part: str) -> FloatArray:
    result: FloatArray = (xy[:, getattr(a, part)] + xy[:, getattr(b, part)]) / 2
    return result


def _flip(values: FloatArray, flipped: BoolArray) -> FloatArray:
    if not flipped.any():
        return values
    result = values.copy()
    result[flipped] = values[flipped][:, MIRROR_ORDER]
    return result


def _replace_spikes(values: FloatArray, spikes: BoolArray) -> FloatArray:
    if not spikes.any():
        return values
    result = values.copy()
    frames, points = np.nonzero(spikes)
    result[frames, points] = (values[frames - 1, points] + values[frames + 1, points]) / 2
    return result
