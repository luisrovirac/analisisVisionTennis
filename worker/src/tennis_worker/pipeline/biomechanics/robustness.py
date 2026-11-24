"""Robustez temporal de las métricas: mediana alrededor del impacto y prueba de sensibilidad.

El fotograma de impacto nunca es exacto. Una métrica cuyo valor cambia mucho si el impacto se mueve dentro
de su incertidumbre no es fiable aunque los puntos del cuerpo se vean bien. Por eso cada métrica se calcula
con el impacto desplazado -K..+K fotogramas, donde K = incertidumbre del impacto × fps (mínimo 1):
±1 fotograma si el impacto lo fijó el sonido; ±0,05 s si se estimó por la velocidad de la muñeca.

- valor = mediana de los desplazamientos -(K-1)..+(K-1): menos sensible a un fotograma ruidoso;
- dispersión = máx - mín en -K..+K; estabilidad = 1 / (1 + (dispersión / tolerancia)²);
- confianza final = confianza original × estabilidad. Con una dispersión igual a la tolerancia, la
  estabilidad es 0,5 y la métrica deja de disparar reglas (umbral de confianza por defecto 0,5).

Idea: tennis-analyzer (MIT; ver Docs/inspiracion02.md), donde el ángulo del codo variaba 10-12° con ±3
fotogramas y se declaraba no fiable. Implementación propia.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

import numpy as np

from tennis_worker.contracts import MetricValue, StrokeType
from tennis_worker.pipeline.biomechanics.base import MetricContext
from tennis_worker.pipeline.biomechanics.registry import MetricRegistry

logger = logging.getLogger(__name__)

# Cuánta variación se considera "significativa" para cada unidad (del orden de la diferencia que separa
# un umbral de error de uno de punto fuerte en las reglas semilla).
DEFAULT_TOLERANCES: Mapping[str, float] = MappingProxyType(
    {"deg": 15.0, "m": 0.05, "ratio": 0.15, "s": 0.1, "bool": 0.5})


def stability(spread: float, tolerance: float) -> float:
    return 1.0 / (1.0 + (spread / tolerance) ** 2)


@dataclass(frozen=True)
class TemporalRobustness:
    """Envuelve el registro de métricas; el pipeline lo usa igual (compute_all)."""

    base: MetricRegistry
    default_uncertainty_s: float = 0.05  # cuando el contexto no trae la incertidumbre del impacto
    enabled: bool = True
    tolerances: Mapping[str, float] = field(default_factory=lambda: DEFAULT_TOLERANCES)

    def __post_init__(self) -> None:
        if self.default_uncertainty_s <= 0:
            raise ValueError("default_uncertainty_s debe ser positiva")

    def window_frames(self, ctx: MetricContext) -> int:
        uncertainty = ctx.impact_uncertainty_s or self.default_uncertainty_s
        return max(1, round(uncertainty * ctx.pose.fps))

    def metric_ids(self, stroke: StrokeType) -> set[str]:
        return self.base.metric_ids(stroke)

    def compute_all(self, stroke: StrokeType, ctx: MetricContext) -> dict[str, MetricValue]:
        nominal = self.base.compute_all(stroke, ctx)
        if not self.enabled:
            return nominal
        window = self.window_frames(ctx)
        runs: dict[int, dict[str, MetricValue]] = {0: nominal}
        for shift in range(-window, window + 1):
            shifted = _shift_impact(ctx, shift)
            if shift and shifted is not None:
                runs[shift] = self.base.compute_all(stroke, shifted)
        return {metric_id: self._robust(metric, runs, window - 1) for metric_id, metric in nominal.items()}

    def _robust(self, nominal: MetricValue, runs: dict[int, dict[str, MetricValue]], median: int) -> MetricValue:
        values = {shift: run[nominal.id].value for shift, run in runs.items()}
        central = [v for s, v in values.items() if abs(s) <= median]
        spread = max(values.values()) - min(values.values())
        tolerance = self.tolerances.get(nominal.unit)
        factor = stability(spread, tolerance) if tolerance else 1.0
        if factor < 0.5:
            logger.info("métrica inestable ante el fotograma de impacto",
                        extra={"metric": nominal.id, "spread": round(spread, 4), "tolerance": tolerance})
        return nominal.model_copy(update={"value": round(float(np.median(central)), 4),
                                          "confidence": round(nominal.confidence * factor, 3)})


def _shift_impact(ctx: MetricContext, shift: int) -> MetricContext | None:
    """El mismo contexto con el impacto desplazado; None si se saldría de la ventana del golpe."""
    seg = ctx.segment
    impact = seg.impact_frame + shift
    if not seg.backswing_frame < impact <= seg.end_frame:
        return None
    return replace(ctx, segment=seg.model_copy(update={"impact_frame": impact}))
