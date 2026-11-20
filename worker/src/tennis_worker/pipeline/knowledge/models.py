"""Esquema de las reglas. Un YAML (o una versión publicada en Postgres) mal formado falla al cargar,
nunca en mitad de un análisis. Espejo de contracts/knowledge_base.schema.json y de la validación de la
API (api/internal/domain/knowledge.go)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tennis_worker.contracts import Category, StrokeType

Operator = Literal["lt", "lte", "gt", "gte", "eq", "between"]
# error = algo a corregir; strength = punto fuerte (tiene su propio umbral, no es "ausencia de error").
Polarity = Literal["error", "strength"]


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
        if isinstance(self.value, tuple) and self.value[0] > self.value[1]:
            raise ValueError("'between' requiere min <= max")
        return self


class ConditionGroup(_Strict):
    all_of: tuple[Condition, ...] | None = Field(default=None, alias="all")
    any_of: tuple[Condition, ...] | None = Field(default=None, alias="any")

    @model_validator(mode="after")
    def _exactly_one(self) -> ConditionGroup:
        if (self.all_of is None) == (self.any_of is None):
            raise ValueError("usa exactamente uno de 'all' o 'any'")
        if not self.conditions:
            raise ValueError("el grupo necesita al menos una condición")
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
    polarity: Polarity = "error"
    impact: int = Field(ge=1, le=5, description="Cuánto mejora (o aporta, si es fortaleza) el golpe")
    difficulty: int = Field(ge=1, le=5, description="Cuánto cuesta corregirlo")
    when: ConditionGroup
    explanation: str
    correction: str = ""  # obligatorio en errores
    why_it_matters: str = ""  # obligatorio en fortalezas
    how_to_maintain: str = ""
    drills: tuple[str, ...] = ()
    caused_by: tuple[str, ...] = ()  # errores que explican este (grafo causal → causa raíz)
    suppressed_by: tuple[str, ...] = ()  # fortaleza que no se elogia si alguno de estos errores aparece
    references: tuple[str, ...] = ()  # bibliografía (p. ej. PMID) o fuentes
    validated: bool = False  # lo fija la API cuando un validador aprueba la regla; nunca el experto

    @property
    def metric_ids(self) -> set[str]:
        return {c.metric for c in self.when.conditions}

    @property
    def is_strength(self) -> bool:
        return self.polarity == "strength"

    @model_validator(mode="after")
    def _texts_by_polarity(self) -> Rule:
        if self.is_strength and not self.why_it_matters.strip():
            raise ValueError(f"{self.id}: una fortaleza necesita 'why_it_matters'")
        if not self.is_strength and not self.correction.strip():
            raise ValueError(f"{self.id}: un error necesita 'correction'")
        if self.is_strength and self.caused_by:
            raise ValueError(f"{self.id}: 'caused_by' solo aplica a errores")
        if not self.is_strength and self.suppressed_by:
            raise ValueError(f"{self.id}: 'suppressed_by' solo aplica a fortalezas")
        return self


class KnowledgeBase(_Strict):
    stroke: StrokeType
    version: str
    validated_by: str | None = None  # legado del YAML; la validación real es por regla (Rule.validated)
    rules: tuple[Rule, ...]

    @property
    def label(self) -> str:
        return f"{self.stroke}@{self.version}"

    @property
    def errors(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if not r.is_strength)

    @property
    def strengths(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.is_strength)

    @model_validator(mode="after")
    def _consistent_references(self) -> KnowledgeBase:
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("hay ids de regla duplicados")
        error_ids = {r.id for r in self.errors}
        for rule in self.rules:
            for ref in (*rule.caused_by, *rule.suppressed_by):
                if ref == rule.id:
                    raise ValueError(f"{rule.id} se referencia a sí misma")
                if ref not in error_ids:
                    raise ValueError(f"{rule.id} referencia '{ref}', que no es un error de esta base")
        return self
