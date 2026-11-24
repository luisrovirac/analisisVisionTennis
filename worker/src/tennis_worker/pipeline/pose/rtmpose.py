"""Pose 2D con RTMPose (OpenMMLab, Apache-2.0) sobre ONNX Runtime.

Dos modelos ONNX oficiales (exportados por MMDeploy, "onnx_sdk"):
- YOLOX-m entrenado en HumanArt: detecta personas (salida con NMS incluido: dets [1,N,5], labels [1,N]).
- RTMPose-m body7 256x192: 17 puntos COCO por persona con cabeza SimCC (simcc_x [N,17,384], simcc_y [N,17,512]).

El preprocesado replica el de MMPose/MMDeploy (pipeline.json de cada modelo): YOLOX recibe BGR sin normalizar
con relleno 114; RTMPose recibe un recorte afín de la caja (margen 1,25, relación 192:256) en RGB normalizado.
Referencia de implementación: rtmlib (Apache-2.0). No se usa rtmlib como dependencia porque exige
opencv-python completo, incompatible con opencv-python-headless del worker.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from tennis_worker.pipeline.types import FloatArray
from tennis_worker.pipeline.video import Frame

COCO_KEYPOINTS = 17
_MEAN = np.array([123.675, 116.28, 103.53])
_STD = np.array([58.395, 57.12, 57.375])


class InferenceSession(Protocol):
    def run(self, output_names: Sequence[str] | None, input_feed: dict[str, Any]) -> list[Any]: ...


def onnx_session(path: Path) -> InferenceSession:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.log_severity_level = 3
    session: InferenceSession = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
    return session


@dataclass(frozen=True)
class Box:
    x1: float
    y1: float
    x2: float
    y2: float
    score: float

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)

    def iou(self, other: Box) -> float:
        ix = max(0.0, min(self.x2, other.x2) - max(self.x1, other.x1))
        iy = max(0.0, min(self.y2, other.y2) - max(self.y1, other.y1))
        inter = ix * iy
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0


# --- Detector -----------------------------------------------------------------------------------------


def letterbox(frame_bgr: Frame, size: int) -> tuple[Frame, float]:
    """Redimensiona manteniendo la proporción y rellena con 114 hasta size x size (esquina superior izq.)."""
    height, width = frame_bgr.shape[:2]
    ratio = min(size / height, size / width)
    resized = cv2.resize(frame_bgr, (int(width * ratio), int(height * ratio)), interpolation=cv2.INTER_LINEAR)
    padded = np.full((size, size, 3), 114, dtype=np.uint8)
    padded[:resized.shape[0], :resized.shape[1]] = resized
    return padded, ratio


@dataclass(frozen=True)
class YoloxPersonDetector:
    session: InferenceSession
    input_size: int = 640
    min_score: float = 0.3

    def detect(self, frame_rgb: Frame) -> list[Box]:
        padded, ratio = letterbox(np.ascontiguousarray(frame_rgb[..., ::-1]), self.input_size)  # el modelo usa BGR
        tensor = padded.transpose(2, 0, 1)[None].astype(np.float32)
        dets, labels = self.session.run(None, {"input": tensor})
        boxes = []
        for (x1, y1, x2, y2, score), label in zip(dets[0], labels[0], strict=True):
            if int(label) == 0 and score >= self.min_score:  # clase 0 = persona
                boxes.append(Box(x1 / ratio, y1 / ratio, x2 / ratio, y2 / ratio, float(score)))
        return boxes


# --- Pose 2D ------------------------------------------------------------------------------------------


def box_center_scale(box: Box, aspect: float, padding: float = 1.25) -> tuple[FloatArray, FloatArray]:
    """Centro y tamaño (w, h) del recorte: caja con margen, ajustada a la relación de aspecto w/h del modelo."""
    center = np.array([(box.x1 + box.x2) / 2, (box.y1 + box.y2) / 2])
    width, height = (box.x2 - box.x1) * padding, (box.y2 - box.y1) * padding
    if width > height * aspect:
        height = width / aspect
    else:
        width = height * aspect
    return center, np.array([width, height])


def crop_matrix(center: FloatArray, scale: FloatArray, out_w: int, out_h: int) -> FloatArray:
    """Transformación afín (sin rotación) que lleva el recorte [center ± scale/2] a [0, out_w] x [0, out_h]."""
    s = out_w / scale[0]
    matrix: FloatArray = np.array([[s, 0.0, out_w / 2 - s * center[0]], [0.0, s, out_h / 2 - s * center[1]]])
    return matrix


def decode_simcc(simcc_x: FloatArray, simcc_y: FloatArray, split_ratio: float = 2.0) -> tuple[FloatArray, FloatArray]:
    """(K, Wx), (K, Wy) → posiciones (K, 2) en píxeles de la entrada del modelo y puntuaciones (K,)."""
    x = np.argmax(simcc_x, axis=1)
    y = np.argmax(simcc_y, axis=1)
    scores = 0.5 * (simcc_x.max(axis=1) + simcc_y.max(axis=1))
    locs = np.stack([x, y], axis=-1).astype(float) / split_ratio
    return locs, scores


@dataclass(frozen=True)
class RtmPoseModel:
    session: InferenceSession
    input_w: int = 192
    input_h: int = 256

    def estimate(self, frame_rgb: Frame, box: Box) -> tuple[FloatArray, FloatArray]:
        """Puntos COCO-17 (17, 2) en píxeles de la imagen original y puntuación (17,) en [0, 1]."""
        center, scale = box_center_scale(box, self.input_w / self.input_h)
        matrix = crop_matrix(center, scale, self.input_w, self.input_h)
        crop = cv2.warpAffine(frame_rgb, matrix, (self.input_w, self.input_h), flags=cv2.INTER_LINEAR)
        tensor = ((crop - _MEAN) / _STD).transpose(2, 0, 1)[None].astype(np.float32)
        simcc_x, simcc_y = self.session.run(None, {"input": tensor})
        locs, scores = decode_simcc(simcc_x[0], simcc_y[0])
        points = locs / np.array([self.input_w, self.input_h]) * scale + center - scale / 2
        return points, np.clip(scores, 0.0, 1.0)


# --- Seguimiento del jugador ---------------------------------------------------------------------------


@dataclass
class PersonTracker:
    """Elige al jugador en cada fotograma: la caja que más solapa con la anterior; si no hay anterior (o se
    perdió), la mayor (el jugador suele ser la persona más cercana a la cámara)."""

    min_iou: float = 0.3
    previous: Box | None = None

    def select(self, boxes: Sequence[Box]) -> Box | None:
        if not boxes:
            return None
        if self.previous is not None:
            best = max(boxes, key=self.previous.iou)
            if self.previous.iou(best) >= self.min_iou:
                self.previous = best
                return best
        self.previous = max(boxes, key=lambda b: b.area * b.score)
        return self.previous
