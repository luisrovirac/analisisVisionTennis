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


class Metric(Protocol):
    id: str
    label: str
    unit: str

    def compute(self, ctx: MetricContext) -> MetricValue: ...


def measured(metric: Metric, value: float, confidence: float) -> MetricValue:
    return MetricValue(id=metric.id, label=metric.label, value=round(float(value), 4),
                       unit=metric.unit, confidence=round(confidence, 3))
