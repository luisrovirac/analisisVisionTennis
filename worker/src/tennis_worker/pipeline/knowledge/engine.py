"""Motor de reglas: evalúa las condiciones del YAML contra las métricas medidas."""

from __future__ import annotations

import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import cast

from tennis_worker.contracts import MetricValue
from tennis_worker.pipeline.knowledge.models import Condition, KnowledgeBase, Operator, Rule

_EQ_TOLERANCE = 1e-6

Threshold = float | tuple[float, float]
OperatorFn = Callable[[float, Threshold], bool]


def _scalar(compare: Callable[[float, float], bool]) -> OperatorFn:
    # El validador de Condition garantiza que los operadores escalares reciben un número.
    return lambda actual, expected: compare(actual, cast(float, expected))


def _between(actual: float, expected: Threshold) -> bool:
    low, high = cast(tuple[float, float], expected)
    return low <= actual <= high


# Registro de operadores (Strategy): añadir uno nuevo no toca el motor.
OPERATORS: Mapping[Operator, OperatorFn] = {
    "lt": _scalar(operator.lt),
    "lte": _scalar(operator.le),
    "gt": _scalar(operator.gt),
    "gte": _scalar(operator.ge),
    "eq": _scalar(lambda a, e: abs(a - e) <= _EQ_TOLERANCE),
    "between": _between,
}


@dataclass(frozen=True)
class FiredRule:
    rule: Rule
    evidence: dict[str, float]


@dataclass(frozen=True)
class Evaluation:
    fired: list[FiredRule]
    unmeasured_metrics: set[str] = field(default_factory=set)


class RuleEngine:
    """Una regla solo se dispara si todas sus métricas se midieron con confianza suficiente:
    es preferible callar a dar una corrección basada en una medición poco fiable."""

    def __init__(self, min_confidence: float) -> None:
        self._min_confidence = min_confidence

    def evaluate(self, kb: KnowledgeBase, metrics: Mapping[str, MetricValue]) -> Evaluation:
        reliable = {k: v for k, v in metrics.items() if v.confidence >= self._min_confidence}
        fired: list[FiredRule] = []
        unmeasured: set[str] = set()
        for rule in kb.rules:
            missing = rule.metric_ids - reliable.keys()
            if missing:
                unmeasured |= missing
            elif _matches(rule, reliable):
                fired.append(FiredRule(rule, {m: reliable[m].value for m in sorted(rule.metric_ids)}))
        return Evaluation(fired=fired, unmeasured_metrics=unmeasured)


def _matches(rule: Rule, metrics: Mapping[str, MetricValue]) -> bool:
    results = (_holds(c, metrics[c.metric].value) for c in rule.when.conditions)
    return all(results) if rule.when.requires_all else any(results)


def _holds(condition: Condition, actual: float) -> bool:
    return OPERATORS[condition.op](actual, condition.value)
