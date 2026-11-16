"""Estimación de pose: interfaz + implementaciones intercambiables."""

from __future__ import annotations

from typing import Protocol

from tennis_worker.pipeline.types import PoseSequence
from tennis_worker.pipeline.video import VideoStream


class PoseEstimator(Protocol):
    def estimate(self, stream: VideoStream) -> PoseSequence: ...
