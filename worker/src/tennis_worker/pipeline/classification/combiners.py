"""Estrategias para combinar clasificadores (STROKE_COMBINER). Todas reciben los clasificadores en el
orden de STROKE_CLASSIFIERS y el mismo umbral de confianza."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence

from tennis_worker.errors import StrokeNotRecognizedError
from tennis_worker.pipeline.classification.base import (
    ClassificationContext,
    ClassificationResult,
    ClassifierCombiner,
    Decision,
    StrokeClassifier,
    as_stroke,
)

USER_ID = "user"


def _results(classifiers: Sequence[StrokeClassifier], ctx: ClassificationContext) -> Iterator[ClassificationResult]:
    """Perezoso: cascade deja de evaluar (y de pagar inferencia) en cuanto uno decide."""
    for classifier in classifiers:
        result = classifier.classify(ctx)
        if result is not None:
            yield result


def _confident(result: ClassificationResult, min_confidence: float) -> Decision | None:
    label, confidence = result.top
    stroke = as_stroke(label)
    if stroke is None or confidence < min_confidence:
        return None
    return Decision.from_result(result, stroke, confidence)


class FirstCombiner:
    """Usa solo el primer clasificador que no se abstiene. Con `user` primero = comportamiento de la etapa 1."""

    def __init__(self, min_confidence: float) -> None:
        self._min = min_confidence

    def decide(self, classifiers: Sequence[StrokeClassifier], ctx: ClassificationContext) -> Decision:
        first = next(_results(classifiers, ctx), None)
        decision = _confident(first, self._min) if first else None
        if decision is None:
            raise StrokeNotRecognizedError(f"primer clasificador: {first.top if first else 'ninguno decidió'}")
        return decision


class CascadeCombiner:
    """El primero que supere el umbral decide; si ninguno lo hace, UNSUPPORTED_STROKE."""

    def __init__(self, min_confidence: float) -> None:
        self._min = min_confidence

    def decide(self, classifiers: Sequence[StrokeClassifier], ctx: ClassificationContext) -> Decision:
        seen = []
        for result in _results(classifiers, ctx):
            decision = _confident(result, self._min)
            if decision is not None:
                return decision
            seen.append(f"{result.classifier_id}={result.top}")
        raise StrokeNotRecognizedError(f"ninguno supera {self._min}: {seen}")


class VoteCombiner:
    """Media ponderada de las probabilidades de todos los clasificadores que no se abstienen."""

    def __init__(self, min_confidence: float, weights: Mapping[str, float]) -> None:
        self._min = min_confidence
        self._weights = weights

    def decide(self, classifiers: Sequence[StrokeClassifier], ctx: ClassificationContext) -> Decision:
        results = list(_results(classifiers, ctx))
        totals: dict[str, float] = defaultdict(float)
        weight_sum = 0.0
        for result in results:
            weight = self._weights.get(result.classifier_id, 1.0)
            weight_sum += weight
            for label, probability in result.probabilities.items():
                totals[label] += weight * probability
        if not results or weight_sum == 0:
            raise StrokeNotRecognizedError("ningún clasificador votó")
        voted = ClassificationResult("vote", {k: v / weight_sum for k, v in totals.items()},
                                     model_version="+".join(f"{r.classifier_id}:{r.model_version}" for r in results))
        decision = _confident(voted, self._min)
        if decision is None:
            raise StrokeNotRecognizedError(f"voto insuficiente: {voted.top}")
        return decision


class VerifyCombiner:
    """El modelo verifica lo que declara el usuario, sin contradecirlo:
    - Si el usuario declaró el golpe, se usa ese golpe. Si un modelo discrepa con confianza ≥ umbral,
      se añade un aviso al reporte.
    - Si no lo declaró, decide el primer modelo que supere el umbral (como cascade)."""

    def __init__(self, min_confidence: float) -> None:
        self._min = min_confidence
        self._cascade = CascadeCombiner(min_confidence)

    def decide(self, classifiers: Sequence[StrokeClassifier], ctx: ClassificationContext) -> Decision:
        models = [c for c in classifiers if c.id != USER_ID]
        declared = ctx.job.stroke_type
        if declared is None:
            return self._cascade.decide(models, ctx)
        warnings = tuple(w for result in _results(models, ctx) if (w := self._disagreement(result, declared)))
        return Decision(stroke=declared, confidence=1.0, classifier_id=USER_ID, warnings=warnings)

    def _disagreement(self, result: ClassificationResult, declared: str) -> str | None:
        label, confidence = result.top
        if label == declared or confidence < self._min:
            return None
        return (f"Parece un golpe de tipo «{label}» ({confidence:.0%}), no «{declared}». "
                "Si te equivocaste al elegirlo, repite el análisis con el golpe correcto.")


CombinerFactory = Callable[[float, Mapping[str, float]], ClassifierCombiner]

COMBINERS: dict[str, CombinerFactory] = {
    "first": lambda threshold, _weights: FirstCombiner(threshold),
    "cascade": lambda threshold, _weights: CascadeCombiner(threshold),
    "vote": lambda threshold, weights: VoteCombiner(threshold, weights),
    "verify": lambda threshold, _weights: VerifyCombiner(threshold),
}
