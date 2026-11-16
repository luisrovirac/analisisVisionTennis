"""Registro de métricas por tipo de golpe (abierto a extensión sin modificar el pipeline)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from tennis_worker.contracts import MetricValue, StrokeType
from tennis_worker.errors import UnsupportedStrokeError
from tennis_worker.pipeline.biomechanics import metrics as m
from tennis_worker.pipeline.biomechanics.base import Metric, MetricContext


class MetricRegistry:
    def __init__(self, metrics_by_stroke: Mapping[StrokeType, Sequence[Metric]]) -> None:
        self._metrics = MappingProxyType({k: tuple(v) for k, v in metrics_by_stroke.items()})

    def supports(self, stroke: StrokeType) -> bool:
        return stroke in self._metrics

    def metric_ids(self, stroke: StrokeType) -> set[str]:
        return {metric.id for metric in self._metrics.get(stroke, ())}

    def compute_all(self, stroke: StrokeType, ctx: MetricContext) -> dict[str, MetricValue]:
        if not self.supports(stroke):
            raise UnsupportedStrokeError(stroke)
        return {metric.id: metric.compute(ctx) for metric in self._metrics[stroke]}


def default_registry() -> MetricRegistry:
    forehand: list[Metric] = [
        m.ShoulderRotationRange(),
        m.HipShoulderSeparationMax(),
        m.ElbowAngleAtImpact(),
        m.KneeAngleMin(),
        m.ContactPointAhead(),
        m.FollowThroughHeight(),
        m.KineticChainSequence(),
    ]
    return MetricRegistry({"forehand": forehand})
