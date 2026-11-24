"""Validación de las reglas con golpes de profesionales, dejando fuera a cada jugador.

Un profesional no debería disparar las reglas de error. Si lo hace, el umbral marca como defecto algo que
hace un buen jugador. Esta validación no basta para calibrar un umbral: falta el lado de los principiantes
(ver Docs/BASE_CONOCIMIENTO.md, «Umbrales: cómo calibrarlos»). Lo que da es una **cota**: hasta dónde puede
llegar el umbral sin castigar a profesionales.

Tres ideas de tennis-analyzer (MIT, docs/decisiones.md y docs/hallazgos.md), con implementación propia:

- **Por sesión, no por golpe.** Un jugador se resume con la mediana de cada métrica sobre sus golpes. Las
  diferencias entre profesionales son mayores que entre golpes de un mismo profesional, y un golpe suelto
  ruidoso dispara reglas que la sesión no dispara.
- **Cota a partir de las medianas por jugador.** Para un defecto «por debajo», la cota es la menor mediana
  de los jugadores menos 0,25 veces la dispersión p10–p90 típica de un jugador (simétrico «por encima»).
  Mínimo 3 jugadores por golpe y vista: con menos, el «rango profesional» es el estilo de esos jugadores.
- **Dejando fuera a cada jugador.** Para saber si la cota generaliza, se calcula sin un jugador y se mira si
  ese jugador queda del lado bueno. Es la tasa de falsas alarmas que cabe esperar con un profesional nuevo.

Las reglas ya llevan el sentido del defecto en el operador (`lt` = defecto por debajo), así que solo cuenta
salirse por ese lado.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from tennis_worker.contracts import CameraView, MetricValue, StrokeType
from tennis_worker.pipeline.knowledge.engine import RuleEngine
from tennis_worker.pipeline.knowledge.models import Condition, KnowledgeBase

MIN_PLAYERS = 3  # jugadores distintos para que una cota tenga sentido
SPREAD_FACTOR = 0.25  # margen: fracción de la dispersión p10–p90 típica de un jugador
MIN_STROKES_FOR_SPREAD = 3  # golpes de un jugador para estimar su dispersión

Direction = Literal["low", "high"]
_DIRECTIONS: Mapping[str, Direction] = {"lt": "low", "lte": "low", "gt": "high", "gte": "high"}


@dataclass(frozen=True)
class ProStroke:
    """Las métricas de un golpe de un profesional, tal como las calcula el pipeline."""

    player: str
    stroke: StrokeType
    view: CameraView
    metrics: Mapping[str, MetricValue]
    source: str = ""  # clip y fotograma, para encontrarlo


@dataclass(frozen=True)
class Session:
    """Un jugador en un golpe y una vista: la mediana de cada métrica medida con fiabilidad."""

    player: str
    stroke: StrokeType
    view: CameraView
    strokes: int
    metrics: Mapping[str, MetricValue]
    samples: Mapping[str, tuple[float, ...]]  # valores fiables por golpe, para la dispersión


def direction(condition: Condition) -> Direction | None:
    """Hacia dónde está el defecto de una condición de error; None si no tiene un único sentido."""
    return _DIRECTIONS.get(condition.op)


def sessions(strokes: Iterable[ProStroke], min_confidence: float) -> list[Session]:
    """Agrupa por (jugador, golpe, vista). Una métrica entra en la sesión si se midió con fiabilidad en al
    menos la mitad de los golpes; si no, la sesión no la tiene (y sus reglas no se evalúan)."""
    groups: dict[tuple[str, StrokeType, CameraView], list[ProStroke]] = defaultdict(list)
    for s in strokes:
        groups[(s.player, s.stroke, s.view)].append(s)
    result = []
    for (player, stroke, view), items in sorted(groups.items()):
        metrics: dict[str, MetricValue] = {}
        samples: dict[str, tuple[float, ...]] = {}
        for metric_id in sorted({k for s in items for k in s.metrics}):
            measured = [s.metrics[metric_id] for s in items if metric_id in s.metrics]
            reliable = tuple(m.value for m in measured if m.confidence >= min_confidence)
            if reliable and 2 * len(reliable) >= len(items):
                metrics[metric_id] = measured[0].model_copy(
                    update={"value": float(np.median(reliable)), "confidence": 1.0})
                samples[metric_id] = reliable
        result.append(Session(player, stroke, view, len(items), metrics, samples))
    return result


def typical_spread(group: Sequence[Session], metric_id: str) -> float:
    """Mediana, entre jugadores con golpes suficientes, de su dispersión p10–p90. 0 si no hay ninguno."""
    spreads = [float(np.percentile(v, 90) - np.percentile(v, 10))
               for s in group if len(v := s.samples.get(metric_id, ())) >= MIN_STROKES_FOR_SPREAD]
    return float(np.median(spreads)) if spreads else 0.0


def pro_bound(group: Sequence[Session], metric_id: str, side: Direction) -> float | None:
    """Hasta dónde llegan los profesionales del grupo por el lado del defecto, con margen. None con menos de
    MIN_PLAYERS jugadores que tengan la métrica."""
    medians = [s.metrics[metric_id].value for s in group if metric_id in s.metrics]
    if len({s.player for s in group if metric_id in s.metrics}) < MIN_PLAYERS:
        return None
    margin = SPREAD_FACTOR * typical_spread(group, metric_id)
    return min(medians) - margin if side == "low" else max(medians) + margin


def beyond(value: float, bound: float, side: Direction) -> bool:
    return value < bound if side == "low" else value > bound


@dataclass(frozen=True)
class HeldOut:
    player: str
    value: float
    bound: float
    flagged: bool  # queda del lado del defecto respecto a la cota de los demás


@dataclass(frozen=True)
class ConditionCheck:
    """Una condición escalar de una regla de error, en un golpe y una vista con datos."""

    rule_id: str
    stroke: StrokeType
    view: CameraView
    condition: Condition
    side: Direction
    players: int
    bound: float | None  # con todos los jugadores
    held_out: tuple[HeldOut, ...]  # vacío si no quedan MIN_PLAYERS al quitar uno
    # Una de varias condiciones que deben cumplirse a la vez: puede ser una puerta («rodilla flexionada» y
    # «sin empuje»), no el defecto. Pasar su cota no basta para castigar a nadie: manda la regla entera.
    combined: bool = False

    @property
    def threshold(self) -> float:
        return float(self.condition.value)  # type: ignore[arg-type]  # solo condiciones escalares

    @property
    def too_strict(self) -> bool:
        """El umbral pasa la cota: alguna mediana de profesional (con margen) estaría del lado del defecto."""
        return not self.combined and self.bound is not None and beyond(self.bound, self.threshold, self.side)

    @property
    def margin(self) -> float | None:
        """Distancia del umbral a la cota, positiva si hay holgura."""
        if self.bound is None:
            return None
        return self.bound - self.threshold if self.side == "low" else self.threshold - self.bound


def leave_one_out(group: Sequence[Session], metric_id: str, side: Direction) -> tuple[HeldOut, ...]:
    """Cada jugador frente a la cota de los demás. Vacío si al quitar uno quedan menos de MIN_PLAYERS."""
    result = []
    for session in group:
        if metric_id not in session.metrics:
            continue
        others = [s for s in group if s.player != session.player]
        bound = pro_bound(others, metric_id, side)
        if bound is None:
            return ()
        value = session.metrics[metric_id].value
        result.append(HeldOut(session.player, value, bound, beyond(value, bound, side)))
    return tuple(result)


def check_conditions(kb: KnowledgeBase, all_sessions: Sequence[Session]) -> list[ConditionCheck]:
    """Las condiciones escalares de las reglas de error, en cada vista con sesiones donde la regla aplica."""
    by_view: dict[CameraView, list[Session]] = defaultdict(list)
    for s in all_sessions:
        if s.stroke == kb.stroke:
            by_view[s.view].append(s)
    checks = []
    for rule in kb.errors:
        for view, group in sorted(by_view.items()):
            if not rule.applies_to(view):
                continue
            combined = rule.when.requires_all and len(rule.when.conditions) > 1
            for condition in rule.when.conditions:
                side = direction(condition)
                if side is None:
                    continue
                players = len({s.player for s in group if condition.metric in s.metrics})
                checks.append(ConditionCheck(rule.id, kb.stroke, view, condition, side, players,
                                             pro_bound(group, condition.metric, side),
                                             leave_one_out(group, condition.metric, side), combined))
    return checks


@dataclass
class FireCount:
    """Cuántas veces se evaluó una regla (aplicaba a la vista y sus métricas eran fiables) y cuántas saltó."""

    evaluated: int = 0
    fired: int = 0
    who: list[str] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.fired / self.evaluated if self.evaluated else math.nan


def fire_counts(kb: KnowledgeBase, items: Iterable[tuple[str, CameraView, Mapping[str, MetricValue]]],
                engine: RuleEngine) -> dict[str, FireCount]:
    """regla → recuento sobre (etiqueta, vista, métricas), sean golpes sueltos o sesiones."""
    counts = {rule.id: FireCount() for rule in kb.rules}
    for label, view, metrics in items:
        evaluation = engine.evaluate(kb, metrics, view)
        fired = {f.rule.id for f in [*evaluation.fired, *evaluation.strengths]}
        reliable = {k for k, v in metrics.items() if v.confidence >= engine.min_confidence}
        for rule in kb.rules:
            if rule.applies_to(view) and rule.metric_ids <= reliable:
                counts[rule.id].evaluated += 1
                if rule.id in fired:
                    counts[rule.id].fired += 1
                    counts[rule.id].who.append(label)
    return counts
