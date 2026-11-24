"""Registro de métricas por tipo de golpe (abierto a extensión sin modificar el pipeline)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from tennis_worker.contracts import MetricValue, StrokeType
from tennis_worker.errors import UnsupportedStrokeError
from tennis_worker.pipeline.biomechanics import metrics as m
from tennis_worker.pipeline.biomechanics.base import Metric, MetricContext


@dataclass(frozen=True)
class CatalogEntry:
    """Lo que el portal de expertos necesita saber de una métrica para componer reglas con ella."""

    stroke: StrokeType
    metric_id: str
    label: str
    unit: str


class MetricComputer(Protocol):
    """Lo que el pipeline necesita: calcular todas las métricas de un golpe (MetricRegistry o un envoltorio
    como TemporalRobustness)."""

    def compute_all(self, stroke: StrokeType, ctx: MetricContext) -> dict[str, MetricValue]: ...


class MetricRegistry:
    def __init__(self, metrics_by_stroke: Mapping[StrokeType, Sequence[Metric]]) -> None:
        self._metrics = MappingProxyType({k: tuple(v) for k, v in metrics_by_stroke.items()})

    def supports(self, stroke: StrokeType) -> bool:
        return stroke in self._metrics

    def metric_ids(self, stroke: StrokeType) -> set[str]:
        return {metric.id for metric in self._metrics.get(stroke, ())}

    def catalog(self) -> list[CatalogEntry]:
        return [CatalogEntry(stroke, metric.id, metric.label, metric.unit)
                for stroke, metrics in self._metrics.items() for metric in metrics]

    def compute_all(self, stroke: StrokeType, ctx: MetricContext) -> dict[str, MetricValue]:
        if not self.supports(stroke):
            raise UnsupportedStrokeError(stroke)
        return {metric.id: metric.compute(ctx) for metric in self._metrics[stroke]}


def default_registry() -> MetricRegistry:
    """Qué mide el worker en cada golpe. Una regla solo puede citar métricas de su golpe; quitar una
    métrica de aquí invalida las versiones publicadas que la usen (el worker las descarta)."""
    groundstroke: list[Metric] = [  # comunes a forehand y revés
        m.ShoulderRotationRange(),
        m.HipShoulderSeparationMax(),
        m.ElbowAngleAtImpact(),
        m.KneeAngleMin(),
        m.ContactPointAhead(),
        m.FollowThroughHeight(),
        m.KineticChainSequence(),
        m.StanceWidthRatio(),
        m.HeadStability(),
        m.RacketDropBelowContact(),
        m.LegDrive(),
        m.UnitTurnLeadTime(),
    ]
    forehand = [*groundstroke, m.OffArmExtension()]
    backhand = [*groundstroke, m.WristSeparationAtImpact()]
    serve: list[Metric] = [
        m.KneeAngleMin(),
        m.LegDrive(),
        m.ElbowAngleAtImpact(),
        m.ContactHeight(),
        m.TrophyElbowAngle(),
        m.TossArmHeight(),
        m.ShoulderTiltAtTrophy(),
        m.ShoulderRotationRange(),
        m.HipShoulderSeparationMax(),
        m.KineticChainSequence(),
    ]
    volley: list[Metric] = [
        m.BackswingWristTravel(),
        m.FollowThroughLength(),
        m.ContactPointAhead(),
        m.ElbowAngleAtImpact(),
        m.KneeAngleMin(),
        m.StanceWidthRatio(),
        m.HeadStability(),
        m.ShoulderRotationRange(),
    ]
    return MetricRegistry({"forehand": forehand, "backhand": backhand, "serve": serve, "volley": volley})
