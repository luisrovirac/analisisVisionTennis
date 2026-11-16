"""Esquema de las reglas. Un YAML mal formado falla al cargar, nunca en mitad de un análisis."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tennis_worker.contracts import Category, StrokeType

Operator = Literal["lt", "lte", "gt", "gte", "eq", "between"]


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class Condition(_Strict):
    metric: str
    op: Operator
    value: float | tuple[float, float]

    @model_validator(mode="after")
    def _value_matches_operator(self) -> Condition:
        if (self.op == "between") != isinstance(self.value, tuple):
            raise ValueError(f"'{self.op}' requiere {'[min, max]' if self.op == 'between' else 'un número'}")
        return self


class ConditionGroup(_Strict):
    all_of: tuple[Condition, ...] | None = Field(default=None, alias="all")
    any_of: tuple[Condition, ...] | None = Field(default=None, alias="any")

    @model_validator(mode="after")
    def _exactly_one(self) -> ConditionGroup:
        if (self.all_of is None) == (self.any_of is None):
            raise ValueError("usa exactamente uno de 'all' o 'any'")
        return self

    @property
    def conditions(self) -> tuple[Condition, ...]:
        return self.all_of or self.any_of or ()

    @property
    def requires_all(self) -> bool:
        return self.all_of is not None


class Rule(_Strict):
    id: str
    title: str
    category: Category
    impact: int = Field(ge=1, le=5, description="Cuánto mejora el golpe al corregirlo")
    difficulty: int = Field(ge=1, le=5, description="Cuánto cuesta corregirlo")
    when: ConditionGroup
    explanation: str
    correction: str
    drills: tuple[str, ...] = ()

    @property
    def metric_ids(self) -> set[str]:
        return {c.metric for c in self.when.conditions}


class KnowledgeBase(_Strict):
    stroke: StrokeType
    version: str
    validated_by: str | None = None
    rules: tuple[Rule, ...]

    @property
    def label(self) -> str:
        return f"{self.stroke}@{self.version}"

    @model_validator(mode="after")
    def _unique_ids(self) -> KnowledgeBase:
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("hay ids de regla duplicados")
        return self
