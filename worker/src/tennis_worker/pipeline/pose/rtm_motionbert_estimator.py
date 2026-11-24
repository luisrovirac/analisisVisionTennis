"""Estimador de pose alternativo: RTMPose (2D) + MotionBERT (3D temporal).

Produce el mismo PoseSequence que MediaPipe (33 posiciones con su numeración), así el resto del pipeline
no cambia. Solo se rellenan las posiciones que usan las métricas (nariz, ojos, orejas, hombros, codos,
muñecas, caderas, rodillas, tobillos); las demás copian la nariz con visibilidad 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from tennis_worker.pipeline.landmarks import LEFT, RIGHT, Landmark
from tennis_worker.pipeline.pose.lifting import MotionBertLifter, coco_to_h36m, normalize_pixels, to_metres
from tennis_worker.pipeline.pose.rtmpose import (
    COCO_KEYPOINTS,
    Box,
    PersonTracker,
    RtmPoseModel,
    YoloxPersonDetector,
)
from tennis_worker.pipeline.types import LANDMARK_COUNT, BoolArray, FloatArray, PoseSequence
from tennis_worker.pipeline.video import VideoStream

# Posición MediaPipe ← índice COCO-17 (2D) y ← índice Human3.6M (3D).
_MP_FROM_COCO = {Landmark.NOSE: 0, 2: 1, 5: 2, 7: 3, 8: 4,
                 LEFT.shoulder: 5, RIGHT.shoulder: 6, LEFT.elbow: 7, RIGHT.elbow: 8, LEFT.wrist: 9, RIGHT.wrist: 10,
                 LEFT.hip: 11, RIGHT.hip: 12, LEFT.knee: 13, RIGHT.knee: 14, LEFT.ankle: 15, RIGHT.ankle: 16}
_MP_FROM_H36M = {Landmark.NOSE: 9, LEFT.shoulder: 11, RIGHT.shoulder: 14, LEFT.elbow: 12, RIGHT.elbow: 15,
                 LEFT.wrist: 13, RIGHT.wrist: 16, LEFT.hip: 4, RIGHT.hip: 1, LEFT.knee: 5, RIGHT.knee: 2,
                 LEFT.ankle: 6, RIGHT.ankle: 3}
_EYES_EARS = (2, 5, 7, 8)  # sin equivalente 3D en Human3.6M: se aproximan con la nariz


@dataclass
class Tracks2D:
    points: FloatArray  # (T, 17, 2) píxeles
    scores: FloatArray  # (T, 17)
    detected: BoolArray  # (T,)
    width: int
    height: int


@dataclass
class RtmPoseMotionBertEstimator:
    detector: YoloxPersonDetector
    pose_model: RtmPoseModel
    lifter: MotionBertLifter
    detect_every: int = 5  # entre detecciones, la caja sale de los puntos del fotograma anterior
    min_pose_score: float = 0.3  # por debajo (media de los 17 puntos), se considera que no hay jugador
    torso_metres: float = 0.5
    _tracker: PersonTracker = field(default_factory=PersonTracker)

    def estimate(self, stream: VideoStream) -> PoseSequence:
        tracks = self.track_2d(stream)
        return self.build(tracks, stream.fps)

    def track_2d(self, stream: VideoStream) -> Tracks2D:
        self._tracker = PersonTracker()
        points, scores, detected = [], [], []
        width = height = 0
        box: Box | None = None
        for index, frame in enumerate(stream.frames()):
            height, width = frame.shape[:2]
            if box is None or index % self.detect_every == 0:
                box = self._tracker.select(self.detector.detect(frame))
            if box is None:
                points.append(np.zeros((COCO_KEYPOINTS, 2)))
                scores.append(np.zeros(COCO_KEYPOINTS))
                detected.append(False)
                continue
            kp, sc = self.pose_model.estimate(frame, box)
            found = float(sc.mean()) >= self.min_pose_score
            points.append(kp)
            scores.append(sc if found else np.zeros(COCO_KEYPOINTS))
            detected.append(found)
            box = _box_from_keypoints(kp, sc) if found else None
        empty = (0, COCO_KEYPOINTS)
        return Tracks2D(np.array(points) if points else np.zeros((*empty, 2)),
                        np.array(scores) if scores else np.zeros(empty),
                        np.array(detected, dtype=bool), width, height)

    def build(self, tracks: Tracks2D, fps: float) -> PoseSequence:
        frames = len(tracks.detected)
        image = np.zeros((frames, LANDMARK_COUNT, 3))
        world = np.zeros((frames, LANDMARK_COUNT, 3))
        visibility = np.zeros((frames, LANDMARK_COUNT))
        if frames == 0 or not tracks.detected.any():
            return PoseSequence(fps=fps, image=image, world=world, visibility=visibility, detected=tracks.detected)
        points = _fill_missing(tracks.points, tracks.detected)
        h36m = coco_to_h36m(points, tracks.scores)
        camera = self.lifter.lift(normalize_pixels(h36m, tracks.width, tracks.height))
        metres = to_metres(camera, self.torso_metres)
        size = np.array([tracks.width, tracks.height])
        image[:] = np.concatenate([points[:, :1] / size, np.zeros((frames, 1, 1))], axis=-1)  # relleno: la nariz
        world[:] = metres[:, 9:10]
        for mp_index, coco_index in _MP_FROM_COCO.items():
            image[:, mp_index, :2] = points[:, coco_index] / size
            visibility[:, mp_index] = tracks.scores[:, coco_index]
        for mp_index, h36m_index in _MP_FROM_H36M.items():
            world[:, mp_index] = metres[:, h36m_index]
        visibility[:, list(_EYES_EARS)] = 0.0  # en 3D son una aproximación: que no sumen confianza
        return PoseSequence(fps=fps, image=image, world=world, visibility=visibility, detected=tracks.detected)


def _box_from_keypoints(points: FloatArray, scores: FloatArray, min_score: float = 0.3) -> Box | None:
    """Caja que envuelve los puntos fiables (el recorte de RTMPose ya añade margen)."""
    good = points[scores >= min_score]
    if len(good) < 4:
        return None
    (x1, y1), (x2, y2) = good.min(axis=0), good.max(axis=0)
    return Box(float(x1), float(y1), float(x2), float(y2), float(scores.mean()))


def _fill_missing(points: FloatArray, detected: BoolArray) -> FloatArray:
    """Interpola linealmente los fotogramas sin jugador (su confianza sigue en 0)."""
    if detected.all():
        return points
    known = np.flatnonzero(detected)
    frames = np.arange(len(points))
    flat = points.reshape(len(points), -1)
    filled = np.column_stack([np.interp(frames, known, flat[known, col]) for col in range(flat.shape[1])])
    result: FloatArray = filled.reshape(points.shape)
    return result
