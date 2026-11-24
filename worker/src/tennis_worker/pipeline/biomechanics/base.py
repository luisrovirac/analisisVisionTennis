"""Interfaz de métrica y contexto compartido para calcularlas."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Protocol

import numpy as np

from tennis_worker.contracts import Handedness, MetricValue, Segment
from tennis_worker.pipeline import geometry
from tennis_worker.pipeline.landmarks import LEFT, RIGHT, BodySides, body_sides
from tennis_worker.pipeline.types import FloatArray, PoseSequence


@dataclass(frozen=True)
class MetricContext:
    pose: PoseSequence
    segment: Segment
    handedness: Handedness
    # Incertidumbre del fotograma de impacto (s). 0 = desconocida (se usa la de TemporalRobustness).
    impact_uncertainty_s: float = 0.0

    @property
    def sides(self) -> BodySides:
        return body_sides(self.handedness)

    @property
    def preparation(self) -> slice:
        """Desde el inicio de la ventana hasta el impacto (incluido)."""
        return slice(self.segment.start_frame, self.segment.impact_frame + 1)

    @property
    def follow_through(self) -> slice:
        return slice(self.segment.impact_frame, self.segment.end_frame + 1)

    @property
    def backswing_to_impact(self) -> slice:
        return slice(self.segment.backswing_frame, self.segment.impact_frame + 1)

    def before_impact(self, seconds: float) -> slice:
        """Los últimos `seconds` antes del impacto (incluido), sin salir de la ventana del golpe."""
        i = self.segment.impact_frame
        return slice(max(self.segment.start_frame, i - round(seconds * self.pose.fps)), i + 1)

    def after_impact(self, seconds: float) -> slice:
        """Los primeros `seconds` desde el impacto (incluido), sin salir de la ventana del golpe."""
        i = self.segment.impact_frame
        return slice(i, min(self.segment.end_frame, i + round(seconds * self.pose.fps)) + 1)

    def around_impact(self, seconds: float) -> slice:
        return slice(self.before_impact(seconds).start, self.after_impact(seconds).stop)

    @cached_property
    def trophy_frame(self) -> int:
        """Saque: fotograma de la "posición de trofeo" ≈ el brazo de lanzamiento (no dominante) en su punto
        más alto antes del impacto."""
        frames = self.preparation
        height = -self.world(self.sides.non_dominant.wrist)[frames, 1]  # y hacia abajo
        return self.segment.start_frame + int(np.argmax(height))

    def world(self, landmark: int) -> FloatArray:
        return self.pose.world[:, landmark]

    @cached_property
    def shoulder_yaw(self) -> FloatArray:
        return geometry.horizontal_yaw(self.world(LEFT.shoulder), self.world(RIGHT.shoulder))

    @cached_property
    def hip_yaw(self) -> FloatArray:
        return geometry.horizontal_yaw(self.world(LEFT.hip), self.world(RIGHT.hip))

    def confidence(self, landmarks: Sequence[int], frames: slice) -> float:
        """Visibilidad media de los landmarks implicados (los fotogramas interpolados cuentan como 0)."""
        visibility = self.pose.visibility[frames][:, list(landmarks)]
        return float(np.clip(visibility.mean(), 0.0, 1.0)) if visibility.size else 0.0

    def support(self, landmarks: Sequence[int], frames: slice) -> Support:
        """La confianza junto con los landmarks que la sustentan (para resaltarlos en el visor)."""
        return Support(self.confidence(landmarks, frames), tuple(sorted({int(i) for i in landmarks})))


@dataclass(frozen=True)
class Support:
    confidence: float
    landmarks: tuple[int, ...]


class Metric(Protocol):
    id: str
    label: str
    unit: str

    def compute(self, ctx: MetricContext) -> MetricValue: ...


def measured(metric: Metric, value: float, support: Support | float) -> MetricValue:
    if isinstance(support, Support):
        confidence, landmarks = support.confidence, list(support.landmarks)
    else:
        confidence, landmarks = support, None
    return MetricValue(id=metric.id, label=metric.label, value=round(float(value), 4),
                       unit=metric.unit, confidence=round(confidence, 3), landmarks=landmarks)
