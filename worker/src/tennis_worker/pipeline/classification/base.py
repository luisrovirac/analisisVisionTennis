"""Tipos e interfaces de la clasificación del golpe.

Un `StrokeClassifier` propone probabilidades por clase (o se abstiene). Un `ClassifierCombiner`
decide el golpe a partir de uno o varios clasificadores. Los dos se eligen por configuración
(STROKE_CLASSIFIERS / STROKE_COMBINER), sin tocar código.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, get_args

from tennis_worker.contracts import JobMessage, StrokeType
from tennis_worker.pipeline.types import PoseSequence

SUPPORTED_STROKES: frozenset[str] = frozenset(get_args(StrokeType))


@dataclass(frozen=True)
class ClassificationContext:
    pose: PoseSequence
    video_path: Path  # los clasificadores sobre fotogramas (YOLO) vuelven a leer el video
    job: JobMessage


@dataclass(frozen=True)
class ClassificationResult:
    """probabilities puede incluir clases que no son golpes soportados (p. ej. "other" = smash)."""

    classifier_id: str
    probabilities: Mapping[str, float]
    model_version: str | None = None
    dataset_hash: str | None = None

    @property
    def top(self) -> tuple[str, float]:
        label = max(self.probabilities, key=lambda k: self.probabilities[k])
        return label, self.probabilities[label]


@dataclass(frozen=True)
class Decision:
    stroke: StrokeType
    confidence: float
    classifier_id: str
    model_version: str | None = None
    dataset_hash: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_result(cls, result: ClassificationResult, stroke: StrokeType, confidence: float) -> Decision:
        return cls(stroke, confidence, result.classifier_id, result.model_version, result.dataset_hash)


class StrokeClassifier(Protocol):
    @property
    def id(self) -> str: ...

    def classify(self, ctx: ClassificationContext) -> ClassificationResult | None:
        """None = se abstiene (p. ej. el usuario no declaró el golpe)."""
        ...


class ClassifierCombiner(Protocol):
    def decide(self, classifiers: Sequence[StrokeClassifier], ctx: ClassificationContext) -> Decision: ...


class StrokeRecognizer:
    """Lo que ve el pipeline: un único paso que compone los clasificadores con la estrategia elegida."""

    def __init__(self, classifiers: Sequence[StrokeClassifier], combiner: ClassifierCombiner) -> None:
        if not classifiers:
            raise ValueError("se necesita al menos un clasificador")
        self._classifiers = tuple(classifiers)
        self._combiner = combiner

    def recognize(self, ctx: ClassificationContext) -> Decision:
        return self._combiner.decide(self._classifiers, ctx)


def as_stroke(label: str) -> StrokeType | None:
    return label if label in SUPPORTED_STROKES else None  # type: ignore[return-value]
