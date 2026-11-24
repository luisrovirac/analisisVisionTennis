"""Criterios para aceptar un video como golpe de referencia (scripts/register_reference.py).

La detección del jugador se mide sobre una *ventana* de fotogramas elegida por nombre en `DETECTION_SCOPES`:

- ``stroke`` (por defecto): solo los fotogramas del golpe (inicio → final del segmento), que son los únicos que
  usa la comparación DTW (`phase_slices`). Un clip corto en el que el jugador entra o sale de cuadro antes o
  después del golpe no se rechaza por ello.
- ``clip``: todos los fotogramas del video (el criterio original, más estricto).

Para probar otro enfoque basta con añadir una función ``(pose, segment) -> slice`` a `DETECTION_SCOPES`: el
script la ofrece automáticamente en ``--detection-scope``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tennis_worker.contracts import PhaseName, Segment
from tennis_worker.pipeline.reference.comparator import phase_slices, phase_visibility
from tennis_worker.pipeline.types import PoseSequence

DetectionScope = Callable[[PoseSequence, Segment], slice]

DETECTION_SCOPES: dict[str, DetectionScope] = {
    "stroke": lambda pose, segment: slice(segment.start_frame, segment.end_frame + 1),
    "clip": lambda pose, segment: slice(0, pose.frame_count),
}
DEFAULT_DETECTION_SCOPE = "stroke"


@dataclass(frozen=True)
class AcceptanceCriteria:
    min_detection_rate: float = 0.9  # una referencia tiene que ser mejor que un análisis normal (0,6)
    min_phase_visibility: float = 0.6
    detection_scope: str = DEFAULT_DETECTION_SCOPE

    def __post_init__(self) -> None:
        if self.detection_scope not in DETECTION_SCOPES:
            raise ValueError(f"detection_scope desconocido: {self.detection_scope!r} "
                             f"(opciones: {', '.join(DETECTION_SCOPES)})")
        for name in ("min_detection_rate", "min_phase_visibility"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} debe estar entre 0 y 1")


@dataclass(frozen=True)
class ReferenceAssessment:
    criteria: AcceptanceCriteria
    detection_rate: float
    detection_frames: slice
    phase_visibility: dict[PhaseName, float]
    problems: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not self.problems


def detection_rate_in(pose: PoseSequence, frames: slice) -> float:
    detected = pose.detected[frames]
    return float(detected.mean()) if detected.size else 0.0


def assess_reference(pose: PoseSequence, segment: Segment,
                     criteria: AcceptanceCriteria = AcceptanceCriteria()) -> ReferenceAssessment:
    frames = DETECTION_SCOPES[criteria.detection_scope](pose, segment)
    rate = detection_rate_in(pose, frames)
    visibility = {name: phase_visibility(pose, sl) for name, sl in phase_slices(segment).items()}
    problems = []
    if rate < criteria.min_detection_rate:
        problems.append(f"detección {rate:.0%} < {criteria.min_detection_rate:.0%} "
                        f"(ámbito «{criteria.detection_scope}»)")
    for name, value in visibility.items():
        if value < criteria.min_phase_visibility:
            problems.append(f"fase {name} con visibilidad {value:.2f} < {criteria.min_phase_visibility}")
    return ReferenceAssessment(criteria=criteria, detection_rate=rate, detection_frames=frames,
                               phase_visibility=visibility, problems=problems)
