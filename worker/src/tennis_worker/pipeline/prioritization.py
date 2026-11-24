"""Ordena los errores de lo más simple/fundamental a lo más complejo."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from tennis_worker.contracts import Category, Finding
from tennis_worker.pipeline.knowledge.engine import FiredRule


class Prioritizer(Protocol):
    def prioritize(self, fired: Sequence[FiredRule]) -> list[Finding]: ...


class CategoryImpactPrioritizer:
    """Primero los errores fundamentales (postura, preparación): corregirlos suele desbloquear los de
    sincronización. Dentro de cada categoría: mayor impacto primero y, a igual impacto, el más fácil."""

    def __init__(self, category_order: Sequence[Category] = ("fundamental", "sincronizacion")) -> None:
        self._order = {category: index for index, category in enumerate(category_order)}

    def prioritize(self, fired: Sequence[FiredRule]) -> list[Finding]:
        ranked = sorted(fired, key=self._sort_key)
        return [_to_finding(item, rank) for rank, item in enumerate(ranked, start=1)]

    def _sort_key(self, item: FiredRule) -> tuple[int, int, int, str]:
        rule = item.rule
        return (self._order.get(rule.category, len(self._order)), -rule.impact, rule.difficulty, rule.id)


def _to_finding(item: FiredRule, rank: int) -> Finding:
    rule = item.rule
    return Finding(
        rule_id=rule.id, rank=rank, category=rule.category, title=rule.title,
        explanation=rule.explanation, correction=rule.correction,
        drills=list(rule.drills), evidence=item.evidence, validated=rule.is_validated,
        validation_status=rule.status,
    )
