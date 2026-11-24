"""Compara cada fase del golpe con la de golpes de referencia del mismo tipo y vista de cámara."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from tennis_worker.contracts import (
    CameraView,
    Handedness,
    PhaseName,
    PhaseSimilarity,
    ReferenceComparison,
    ReferenceUsed,
    Segment,
    StrokeType,
)
from tennis_worker.pipeline.reference.dtw import dtw
from tennis_worker.pipeline.reference.features import FEATURE_LANDMARKS, frame_features, resample
from tennis_worker.pipeline.types import FloatArray, PoseSequence

_MIN_PHASE_FRAMES = 3


@dataclass(frozen=True)
class ReferenceStroke:
    """Golpe modelo registrado con scripts/register_reference.py (contracts/reference_stroke.schema.json)."""

    ref_id: str
    label: str
    stroke: StrokeType
    camera_view: CameraView
    handedness: Handedness
    source: str
    pose: PoseSequence
    segment: Segment


class ReferenceProvider(Protocol):
    def references(self, stroke: StrokeType, view: CameraView) -> list[ReferenceStroke]: ...


def phase_slices(segment: Segment) -> dict[PhaseName, slice]:
    """Preparación (inicio → armado), aceleración (armado → impacto) y terminación (impacto → final)."""
    return {
        "preparation": slice(segment.start_frame, segment.backswing_frame + 1),
        "acceleration": slice(segment.backswing_frame, segment.impact_frame + 1),
        "follow_through": slice(segment.impact_frame, segment.end_frame + 1),
    }


@dataclass
class ReferenceComparator:
    provider: ReferenceProvider
    min_confidence: float = 0.5  # el mismo umbral que las métricas (MIN_METRIC_CONFIDENCE)
    similarity_scale_deg: float = 20.0  # desviación media (grados) que da similitud 100·e⁻¹ ≈ 37
    band: float = 0.3
    # Clave (ref_id, id de la pose): una referencia re-registrada llega como objeto nuevo y se recalcula.
    _cache: dict[tuple[str, int], dict[PhaseName, FloatArray]] = field(default_factory=dict, repr=False)

    def compare(self, pose: PoseSequence, segment: Segment, handedness: Handedness, stroke: StrokeType,
                view: CameraView) -> ReferenceComparison:
        references = self.provider.references(stroke, view)
        if not references:
            return ReferenceComparison(status="no_references",
                                       note="Aún no hay golpes de referencia para este golpe y vista de cámara.")
        user = _phase_features(pose, segment, handedness)
        confidence = {name: phase_visibility(pose, frames) for name, frames in phase_slices(segment).items()}
        best: tuple[float, ReferenceStroke, dict[PhaseName, float]] | None = None
        for ref in references:
            costs = self._costs(user, confidence, ref)
            if not costs:
                continue
            mean = sum(costs.values()) / len(costs)
            if best is None or mean < best[0]:
                best = (mean, ref, costs)
        phases = [self._phase(name, confidence[name], user[name], best[2] if best else {})
                  for name in phase_slices(segment)]
        if best is None:
            return ReferenceComparison(status="low_confidence", phases=phases,
                                       note="El jugador no se vio con suficiente seguridad para comparar el golpe.")
        scored = [p.similarity for p in phases if p.similarity is not None]
        # Una fase demasiado corta (p. ej. sin armado visible) no es una abstención: no cuenta como parcial.
        status = "partial" if any(p.status == "low_confidence" for p in phases) else "ok"
        return ReferenceComparison(
            status=status, references_used=[ReferenceUsed(ref_id=best[1].ref_id, label=best[1].label)],
            overall_similarity=round(sum(scored) / len(scored), 1), phases=phases,
            note="Similitud informativa con un golpe modelo (ángulos articulares alineados en el tiempo); "
                 "no sustituye al diagnóstico de las reglas.")

    def _costs(self, user: dict[PhaseName, FloatArray], confidence: dict[PhaseName, float],
               ref: ReferenceStroke) -> dict[PhaseName, float]:
        key = (ref.ref_id, id(ref.pose))
        if key not in self._cache:
            self._cache[key] = _phase_features(ref.pose, ref.segment, ref.handedness)
        reference = self._cache[key]
        return {name: dtw(user[name], reference[name], self.band).cost for name in user
                if confidence[name] >= self.min_confidence
                and len(user[name]) >= _MIN_PHASE_FRAMES and len(reference[name]) >= _MIN_PHASE_FRAMES}

    def _phase(self, name: PhaseName, confidence: float, features: FloatArray,
               costs: dict[PhaseName, float]) -> PhaseSimilarity:
        conf = round(confidence, 3)
        if len(features) < _MIN_PHASE_FRAMES:
            return PhaseSimilarity(phase=name, status="too_short", confidence=conf)
        if name not in costs:
            return PhaseSimilarity(phase=name, status="low_confidence", confidence=conf)
        similarity = 100.0 * math.exp(-costs[name] / self.similarity_scale_deg)
        return PhaseSimilarity(phase=name, status="ok", similarity=round(similarity, 1), confidence=conf)


def _phase_features(pose: PoseSequence, segment: Segment, handedness: Handedness) -> dict[PhaseName, FloatArray]:
    features = frame_features(pose, handedness, segment.impact_frame)
    return {name: resample(features[frames], pose.fps) for name, frames in phase_slices(segment).items()}


def phase_visibility(pose: PoseSequence, frames: slice) -> float:
    """Visibilidad media de los landmarks implicados (interpolados = 0), como MetricContext.confidence."""
    visibility = pose.visibility[frames][:, list(FEATURE_LANDMARKS)]
    return float(np.clip(visibility.mean(), 0.0, 1.0)) if visibility.size else 0.0
