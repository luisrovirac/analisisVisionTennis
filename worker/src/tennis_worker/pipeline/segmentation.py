"""Segmentación del golpe: localiza preparación, impacto y terminación dentro del video.

`PeakStrokeSegmenter` encuentra todos los golpes del video; `WristSpeedSegmenter` (la versión anterior, un
solo golpe) se conserva como línea base del banco de pruebas (scripts/bench_segmentation.py).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np

from tennis_worker.contracts import Handedness, Segment, StrokeType
from tennis_worker.errors import NoStrokeFoundError
from tennis_worker.pipeline import geometry
from tennis_worker.pipeline.landmarks import LEFT, RIGHT, Landmark, body_sides
from tennis_worker.pipeline.types import BoolArray, FloatArray, PoseSequence


class StrokeSegmenter(Protocol):
    def segment(self, pose: PoseSequence, handedness: Handedness, stroke: StrokeType | None = None) -> Segment: ...


@dataclass(frozen=True)
class WristSpeedSegmenter:
    """Impacto ≈ pico de velocidad de la muñeca dominante (aprox. razonable para golpes de fondo).

    Limitación conocida: asume un único golpe por video (toma el pico más alto).
    """

    window_seconds: float = 1.0
    smoothing_seconds: float = 0.07
    min_peak_speed: float = 1.5  # m/s, relativa a la cadera

    def segment(self, pose: PoseSequence, handedness: Handedness, stroke: StrokeType | None = None) -> Segment:
        del stroke  # no distingue el saque
        fps = pose.fps
        wrist = pose.world[:, body_sides(handedness).dominant.wrist]
        wrist_speed = geometry.smooth(geometry.speed(wrist, fps), max(1, round(self.smoothing_seconds * fps)))
        impact = int(np.argmax(wrist_speed))
        if wrist_speed[impact] < self.min_peak_speed:
            raise NoStrokeFoundError(f"pico de muñeca {wrist_speed[impact]:.2f} m/s")
        return self.segment_at(pose, handedness, impact)

    def segment_at(self, pose: PoseSequence, handedness: Handedness, impact: int) -> Segment:
        """Ventana y backswing alrededor de un impacto ya conocido (p. ej. el que fija el audio)."""
        del handedness  # el backswing se mide con los hombros, igual para diestros y zurdos
        return window_around(pose, impact, self.window_seconds)


def window_around(pose: PoseSequence, impact: int, window_seconds: float) -> Segment:
    half = round(window_seconds * pose.fps)
    start, end = max(0, impact - half), min(pose.frame_count - 1, impact + half)
    return Segment(fps=pose.fps, start_frame=start, backswing_frame=_backswing(pose, start, impact),
                   impact_frame=impact, end_frame=end)


def _backswing(pose: PoseSequence, start: int, impact: int) -> int:
    """Fotograma de máxima rotación de hombros respecto a su orientación en el impacto."""
    yaw = geometry.horizontal_yaw(pose.world[:, LEFT.shoulder], pose.world[:, RIGHT.shoulder])
    turn = np.abs(geometry.wrap_degrees(yaw[start:impact + 1] - yaw[impact]))
    return start + int(np.argmax(turn))


Signal = Literal["world", "image"]
StrokeKind = Literal["groundstroke", "serve"]


@dataclass(frozen=True)
class StrokeCandidate:
    impact: int
    kind: StrokeKind
    score: float  # velocidad del pico (golpe de fondo) o altura de la muñeca sobre la nariz (saque)


@dataclass(frozen=True)
class PeakStrokeSegmenter:
    """Todos los golpes del video. Portado y adaptado de tennis-analyzer (scripts/segmentar_golpes.py, MIT).

    Golpes de fondo (y volea): picos de velocidad de la muñeca dominante respecto a la cadera que destacan
    `relative_prominence` veces sobre la mediana del propio clip, separados al menos `min_separation_seconds`,
    y por encima de un mínimo absoluto (jugador quieto = sin golpe). Umbral relativo y no solo absoluto: lo
    que es "rápido" depende del encuadre y del estimador.

    Saque: geometría, no velocidad (un saque tiene picos en el lanzamiento, el trofeo, la caída de la raqueta
    y la terminación). Impacto = punto más alto de la muñeca dominante en cada episodio en que supera
    `serve_height` largos de torso sobre la nariz. Si no hay ninguno (cabeza fuera de plano, saque raro) se
    usan los picos de velocidad.

    Señal: "world" (m/s, coordenadas world de la pose, ya centradas en la cadera) o "image" (largos de torso
    por segundo en el plano de la imagen, como el original). Se elige con el banco de pruebas.

    `segment` devuelve un solo golpe (el contrato del informe es de un golpe): el candidato de mayor
    puntuación. Si ningún pico destaca pero el máximo supera el mínimo absoluto, se usa el máximo, que es lo
    que hacía `WristSpeedSegmenter`: un clip de un solo golpe sin apenas reposo no se queda sin análisis.
    """

    signal: Signal = "image"
    window_seconds: float = 1.0
    smoothing_seconds: float = 0.07
    relative_prominence: float = 3.0
    min_separation_seconds: float = 0.6
    min_peak_speed_world: float = 1.5  # m/s
    min_peak_speed_image: float = 3.0  # largos de torso por segundo (~1,5 m/s con un torso de 0,5 m)
    serve_height: float = 0.8  # largos de torso de la muñeca sobre la nariz
    serve_merge_seconds: float = 0.17  # episodios de saque más cercanos se unen

    def candidates(self, pose: PoseSequence, handedness: Handedness,
                   stroke: StrokeType | None = None) -> list[StrokeCandidate]:
        """Golpes del video en orden temporal. Con stroke="serve" solo saques (o picos si no hay ninguno); con
        otro golpe, solo golpes de fondo; sin golpe conocido, ambos (una preparación alta puede pasar por
        saque: mejor indicarlo)."""
        if pose.frame_count < 3 or not pose.detected.any():
            return []
        serves = self._serves(pose, handedness) if stroke in ("serve", None) else []
        if stroke == "serve" and serves:
            return serves
        groundstrokes = self._groundstrokes(pose, handedness, exclude=serves)
        return sorted([*serves, *groundstrokes], key=lambda c: c.impact)

    def segment(self, pose: PoseSequence, handedness: Handedness, stroke: StrokeType | None = None) -> Segment:
        found = self.candidates(pose, handedness, stroke)
        if found:
            return self.segment_at(pose, handedness, max(found, key=lambda c: c.score).impact)
        speed = self.wrist_speed(pose, handedness) if pose.frame_count else np.zeros(0)
        if not speed.size:
            raise NoStrokeFoundError("sin pose")
        impact = int(np.argmax(speed))
        if speed[impact] < self.min_speed:
            raise NoStrokeFoundError(f"pico de muñeca {speed[impact]:.2f} ({self.signal})")
        return self.segment_at(pose, handedness, impact)

    def segment_at(self, pose: PoseSequence, handedness: Handedness, impact: int) -> Segment:
        """Ventana y backswing alrededor de un impacto ya conocido (p. ej. el que fija el audio)."""
        del handedness
        return window_around(pose, impact, self.window_seconds)

    def segments(self, pose: PoseSequence, handedness: Handedness,
                 stroke: StrokeType | None = None) -> list[Segment]:
        return [self.segment_at(pose, handedness, c.impact) for c in self.candidates(pose, handedness, stroke)]

    @property
    def min_speed(self) -> float:
        return self.min_peak_speed_world if self.signal == "world" else self.min_peak_speed_image

    def wrist_speed(self, pose: PoseSequence, handedness: Handedness) -> FloatArray:
        """Velocidad suavizada de la muñeca dominante respecto a la cadera (unidades según `signal`)."""
        wrist = body_sides(handedness).dominant.wrist
        if self.signal == "world":
            relative = pose.world[:, wrist]  # world ya está centrado en la cadera
        else:
            xy = _image_xy(pose)
            relative = (xy[:, wrist] - _hip(xy)) / _rolling_median(_torso(xy), 15)[:, None]
        speed = np.nan_to_num(geometry.speed(relative, pose.fps), nan=0.0)
        return geometry.smooth(speed, max(1, round(self.smoothing_seconds * pose.fps)))

    def wrist_height(self, pose: PoseSequence, handedness: Handedness) -> FloatArray:
        """Largos de torso de la muñeca dominante por encima de la nariz (positivo = más alta; y hacia abajo)."""
        wrist = body_sides(handedness).dominant.wrist
        points = pose.world[..., :2] if self.signal == "world" else _image_xy(pose)
        torso = _rolling_median(_torso(points), 15)
        with np.errstate(invalid="ignore", divide="ignore"):
            height: FloatArray = (points[:, Landmark.NOSE, 1] - points[:, wrist, 1]) / torso
        return height

    def _groundstrokes(self, pose: PoseSequence, handedness: Handedness,
                       exclude: list[StrokeCandidate]) -> list[StrokeCandidate]:
        speed = self.wrist_speed(pose, handedness)
        base = float(np.median(speed)) or float(np.mean(speed))  # mediana 0: clip quieto salvo el golpe
        separation = max(1, round(self.min_separation_seconds * pose.fps))
        peaks = find_peaks(speed, prominence=self.relative_prominence * base, distance=separation)
        return [StrokeCandidate(p, "groundstroke", float(speed[p])) for p in peaks
                if speed[p] >= self.min_speed and all(abs(p - s.impact) > separation for s in exclude)]

    def _serves(self, pose: PoseSequence, handedness: Handedness) -> list[StrokeCandidate]:
        height = self.wrist_height(pose, handedness)
        valid = pose.detected & np.isfinite(height)
        smoothed = geometry.smooth(np.where(valid, height, -1e9), max(1, round(0.08 * pose.fps)))
        episodes = merge_runs(runs(smoothed > self.serve_height), max(1, round(self.serve_merge_seconds * pose.fps)))
        result = []
        for start, end in episodes:
            window = np.where(valid[start:end], height[start:end], -np.inf)
            if np.isfinite(window).any():
                impact = start + int(np.argmax(window))
                result.append(StrokeCandidate(impact, "serve", float(height[impact])))
        return result


def find_peaks(x: FloatArray, prominence: float, distance: int) -> list[int]:
    """Máximos locales con prominencia >= `prominence` y separados al menos `distance` muestras (se quedan
    los más altos). Equivalente sencillo de scipy.signal.find_peaks, para no añadir scipy al worker."""
    if len(x) < 3:
        return []
    interior = np.flatnonzero((x[1:-1] > x[:-2]) & (x[1:-1] >= x[2:])) + 1
    prominent = [int(p) for p in interior if peak_prominence(x, int(p)) >= prominence]
    chosen: list[int] = []
    for p in sorted(prominent, key=lambda p: -x[p]):
        if all(abs(p - q) >= distance for q in chosen):
            chosen.append(p)
    return sorted(chosen)


def peak_prominence(x: FloatArray, peak: int) -> float:
    """Altura del pico sobre la más alta de sus dos bases: a cada lado, el mínimo hasta el siguiente punto
    más alto que el pico (o hasta el borde)."""
    bases = []
    for side in (x[:peak][::-1], x[peak + 1:]):
        higher = np.flatnonzero(side > x[peak])
        stretch = side[:higher[0]] if higher.size else side
        bases.append(float(stretch.min()) if stretch.size else float(x[peak]))
    return float(x[peak]) - max(bases)


def runs(mask: BoolArray) -> list[tuple[int, int]]:
    """Tramos [inicio, fin) de True consecutivos."""
    edges = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist(), strict=True))


def merge_runs(spans: list[tuple[int, int]], gap: int) -> list[tuple[int, int]]:
    """Une tramos separados por menos de `gap` muestras."""
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start - merged[-1][1] < gap:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _image_xy(pose: PoseSequence) -> FloatArray:
    xy = pose.image[..., :2].copy()
    if pose.frame_size is not None:
        xy *= np.array(pose.frame_size, dtype=float)
    return xy


def _hip(points: FloatArray) -> FloatArray:
    hip: FloatArray = (points[:, LEFT.hip] + points[:, RIGHT.hip]) / 2
    return hip


def _torso(points: FloatArray) -> FloatArray:
    shoulders = (points[:, LEFT.shoulder] + points[:, RIGHT.shoulder]) / 2
    length = np.linalg.norm(shoulders - _hip(points), axis=-1)
    with np.errstate(invalid="ignore"):
        return np.where(length > 0, length, np.nan)


def _rolling_median(x: FloatArray, window: int) -> FloatArray:
    """Mediana móvil centrada que ignora NaN: estabiliza el largo del torso, que cambia con el giro."""
    half = window // 2
    windows = np.lib.stride_tricks.sliding_window_view(np.pad(x, (half, half), mode="edge"), window)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # ventanas enteras sin detección → NaN
        result: FloatArray = np.nanmedian(windows, axis=1)
    return result
