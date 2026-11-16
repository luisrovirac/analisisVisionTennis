"""Estimador de pose con MediaPipe Pose Landmarker (Tasks API, modo VIDEO)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision

from tennis_worker.pipeline.pose.builder import PoseSequenceBuilder
from tennis_worker.pipeline.types import PoseSequence
from tennis_worker.pipeline.video import VideoStream


class MediaPipePoseEstimator:
    def __init__(self, model_path: Path, min_detection_confidence: float = 0.5) -> None:
        self._options = vision.PoseLandmarkerOptions(
            base_options=mp_tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_detection_confidence,
        )
        # Fail fast: carga el modelo y las librerías nativas al arrancar, no en el primer video.
        vision.PoseLandmarker.create_from_options(self._options).close()

    def estimate(self, stream: VideoStream) -> PoseSequence:
        builder = PoseSequenceBuilder(stream.fps)
        with vision.PoseLandmarker.create_from_options(self._options) as landmarker:
            for index, frame in enumerate(stream.frames()):
                timestamp_ms = int(index * 1000 / stream.fps)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame)
                _add_result(builder, landmarker.detect_for_video(image, timestamp_ms))
        return builder.build()


def _add_result(builder: PoseSequenceBuilder, result: Any) -> None:
    if not result.pose_landmarks:
        builder.add_missing()
        return
    image_lms, world_lms = result.pose_landmarks[0], result.pose_world_landmarks[0]
    builder.add_detection(
        image=[(lm.x, lm.y, lm.z) for lm in image_lms],
        world=[(lm.x, lm.y, lm.z) for lm in world_lms],
        visibility=[lm.visibility or 0.0 for lm in image_lms],
    )
