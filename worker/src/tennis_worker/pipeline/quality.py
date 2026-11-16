"""Evaluación de la calidad de entrada: bloquea análisis no fiables y genera avisos accionables."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tennis_worker.contracts import JobMessage
from tennis_worker.errors import LowPoseQualityError, NoPersonDetectedError
from tennis_worker.pipeline.types import PoseSequence


@dataclass(frozen=True)
class QualityAssessor:
    min_detection_rate: float
    min_fps: float = 30.0

    def check(self, pose: PoseSequence) -> None:
        if not pose.detected.any():
            raise NoPersonDetectedError
        if pose.detection_rate < self.min_detection_rate:
            raise LowPoseQualityError(f"detección {pose.detection_rate:.0%}")

    def warnings(self, pose: PoseSequence, job: JobMessage, unmeasured_labels: Iterable[str]) -> list[str]:
        result: list[str] = []
        if pose.fps < self.min_fps:
            result.append(f"El video está a {pose.fps:.0f} fps; graba a 60 fps para medir mejor el impacto.")
        if job.camera_view != "side":
            result.append("Las métricas están calibradas para vista de perfil; los resultados con esta vista "
                          "son orientativos.")
        result.extend(f"No pudimos medir con fiabilidad: {label}. Revisa que se vea todo el cuerpo."
                      for label in sorted(unmeasured_labels))
        return result
