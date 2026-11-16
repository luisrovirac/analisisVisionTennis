"""Segmentación del golpe: localiza preparación, impacto y terminación dentro del video."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from tennis_worker.contracts import Handedness, Segment
from tennis_worker.errors import NoStrokeFoundError
from tennis_worker.pipeline import geometry
from tennis_worker.pipeline.landmarks import LEFT, RIGHT, body_sides
from tennis_worker.pipeline.types import PoseSequence


class StrokeSegmenter(Protocol):
    def segment(self, pose: PoseSequence, handedness: Handedness) -> Segment: ...


@dataclass(frozen=True)
class WristSpeedSegmenter:
    """Impacto ≈ pico de velocidad de la muñeca dominante (aprox. razonable para golpes de fondo).

    Limitación conocida: asume un único golpe por video (toma el pico más alto).
    """

    window_seconds: float = 1.0
    smoothing_seconds: float = 0.07
    min_peak_speed: float = 1.5  # m/s, relativa a la cadera

    def segment(self, pose: PoseSequence, handedness: Handedness) -> Segment:
        fps = pose.fps
        wrist = pose.world[:, body_sides(handedness).dominant.wrist]
        wrist_speed = geometry.smooth(geometry.speed(wrist, fps), max(1, round(self.smoothing_seconds * fps)))
        impact = int(np.argmax(wrist_speed))
        if wrist_speed[impact] < self.min_peak_speed:
            raise NoStrokeFoundError(f"pico de muñeca {wrist_speed[impact]:.2f} m/s")
        half = round(self.window_seconds * fps)
        start, end = max(0, impact - half), min(pose.frame_count - 1, impact + half)
        return Segment(fps=fps, start_frame=start, backswing_frame=self._backswing(pose, start, impact),
                       impact_frame=impact, end_frame=end)

    @staticmethod
    def _backswing(pose: PoseSequence, start: int, impact: int) -> int:
        """Fotograma de máxima rotación de hombros respecto a su orientación en el impacto."""
        yaw = geometry.horizontal_yaw(pose.world[:, LEFT.shoulder], pose.world[:, RIGHT.shoulder])
        turn = np.abs(geometry.wrap_degrees(yaw[start:impact + 1] - yaw[impact]))
        return start + int(np.argmax(turn))
