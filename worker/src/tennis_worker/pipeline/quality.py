"""Evaluación de la calidad de entrada: bloquea análisis no fiables y genera avisos accionables.

Los avisos siguen el protocolo de grabación (Docs/BASE_CONOCIMIENTO.md): trípode, cuerpo entero, 60 fps
o más, jugador grande en la imagen."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from tennis_worker.contracts import JobMessage
from tennis_worker.errors import LowPoseQualityError, NoPersonDetectedError
from tennis_worker.pipeline.landmarks import LEFT, RIGHT, Landmark
from tennis_worker.pipeline.types import PoseSequence

_EXTREMITIES = (Landmark.NOSE, LEFT.ankle, RIGHT.ankle)


@dataclass(frozen=True)
class QualityAssessor:
    min_detection_rate: float
    min_fps: float = 50.0  # por debajo, el impacto cae entre fotogramas y la muñeca sale borrosa
    min_torso_height: float = 0.12  # fracción de la altura de la imagen (hombros → cadera)
    min_extremity_visibility: float = 0.4

    def check(self, pose: PoseSequence) -> None:
        if not pose.detected.any():
            raise NoPersonDetectedError
        if pose.detection_rate < self.min_detection_rate:
            raise LowPoseQualityError(f"detección {pose.detection_rate:.0%}")

    def warnings(self, pose: PoseSequence, job: JobMessage, unmeasured_labels: Iterable[str]) -> list[str]:
        result: list[str] = []
        if pose.fps < self.min_fps:
            result.append(f"El video está a {pose.fps:.0f} fps; graba a 60 fps o más (o en cámara lenta) para "
                          "medir mejor el impacto.")
        if job.camera_view != "side":
            result.append("La mayoría de las métricas están pensadas para vista de perfil; con esta vista los "
                          "resultados son orientativos.")
        result.extend(self._framing(pose))
        labels = sorted(unmeasured_labels)
        if labels:
            result.append(f"No pudimos medir con fiabilidad: {'; '.join(labels)}. Suele deberse a partes del cuerpo "
                          "poco visibles o a que el video tiene pocos fps para un movimiento tan rápido.")
        return result

    def _framing(self, pose: PoseSequence) -> list[str]:
        detected = pose.detected
        if not detected.any():
            return []
        result: list[str] = []
        image = pose.image[detected]
        shoulders = image[:, [LEFT.shoulder, RIGHT.shoulder], 1].mean(axis=1)
        hips = image[:, [LEFT.hip, RIGHT.hip], 1].mean(axis=1)
        if float(np.median(np.abs(hips - shoulders))) < self.min_torso_height:
            result.append("El jugador se ve pequeño en la imagen: acerca la cámara o haz zoom (que ocupe al menos "
                          "media altura del video).")
        visibility = pose.visibility[detected][:, list(_EXTREMITIES)]
        if float(np.median(visibility.min(axis=1))) < self.min_extremity_visibility:
            result.append("Parte del cuerpo queda fuera de la imagen o tapada (cabeza o pies): encuadra el cuerpo "
                          "entero durante todo el golpe.")
        return result
