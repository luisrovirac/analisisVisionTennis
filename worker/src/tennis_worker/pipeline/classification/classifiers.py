"""Implementaciones de StrokeClassifier: lo que declara el usuario, un modelo sobre la secuencia
de pose (GRU/1D-CNN exportado a ONNX) y un modelo YOLO sobre fotogramas."""

from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray

from tennis_worker.pipeline.classification.base import ClassificationContext, ClassificationResult
from tennis_worker.pipeline.classification.features import FEATURE_SET, sequence_features
from tennis_worker.pipeline.classification.model import OnnxModel, softmax
from tennis_worker.pipeline.video import Frame, VideoReader


class UserProvidedClassifier:
    id = "user"

    def classify(self, ctx: ClassificationContext) -> ClassificationResult | None:
        if ctx.job.stroke_type is None:
            return None
        return ClassificationResult(self.id, {ctx.job.stroke_type: 1.0})


class PoseSequenceClassifier:
    """Reutiliza la pose de MediaPipe que el pipeline ya calculó: cuesta milisegundos en CPU."""

    id = "pose_seq"

    def __init__(self, model: OnnxModel) -> None:
        if model.meta.feature_set != FEATURE_SET or model.meta.seq_len is None:
            raise ValueError(
                f"modelo pose_seq entrenado con feature_set={model.meta.feature_set!r}; el worker usa "
                f"{FEATURE_SET!r}. Reentrena o despliega un modelo compatible.")
        self._model = model
        self._seq_len = model.meta.seq_len

    def classify(self, ctx: ClassificationContext) -> ClassificationResult:
        features = sequence_features(ctx.pose, ctx.job.handedness, self._seq_len)
        probs = softmax(self._model.run(features[np.newaxis])[0])
        return _result(self.id, self._model, probs)


class YoloFrameClassifier:
    """Modelo YOLO (detección o pose) cuyas clases son golpes. Puntúa fotogramas muestreados y vota.

    La puntuación de un fotograma para cada clase es la máxima entre todas sus detecciones; la del
    clip es la media entre fotogramas. Son confianzas independientes por clase (sigmoides), no una
    distribución: se comparan con STROKE_MIN_CONFIDENCE igual que las del resto de clasificadores.
    """

    id = "yolo"

    def __init__(self, model: OnnxModel, video_reader: VideoReader) -> None:
        if model.meta.imgsz is None or model.meta.frames is None:
            raise ValueError("el meta.json de yolo necesita imgsz y frames")
        self._model = model
        self._reader = video_reader
        self._imgsz = model.meta.imgsz
        self._wanted = model.meta.frames

    def classify(self, ctx: ClassificationContext) -> ClassificationResult:
        scores = [self._frame_scores(frame) for frame in self._sample(ctx)]
        mean = np.mean(scores, axis=0) if scores else np.zeros(len(self._model.meta.classes))
        return _result(self.id, self._model, mean)

    def _sample(self, ctx: ClassificationContext) -> list[Frame]:
        total = ctx.pose.frame_count  # el mismo video ya se leyó para la pose: se conoce su longitud
        wanted = set(np.linspace(0, max(total - 1, 0), min(self._wanted, max(total, 1))).round().astype(int))
        with self._reader.open(ctx.video_path) as stream:
            return [frame for index, frame in enumerate(stream.frames()) if index in wanted]

    def _frame_scores(self, frame: Frame) -> NDArray[np.float64]:
        output = self._model.run(letterbox(frame, self._imgsz)[np.newaxis])[0]  # (4 + nc [+ kpts], anchors)
        class_scores = output[4:4 + len(self._model.meta.classes)]
        return class_scores.max(axis=1).astype(np.float64)  # type: ignore[no-any-return]


def _result(classifier_id: str, model: OnnxModel, values: NDArray[np.float64]) -> ClassificationResult:
    """strict=True: si la salida del modelo no tiene una puntuación por clase de meta.json, falla."""
    meta = model.meta
    probabilities = dict(zip(meta.classes, values.tolist(), strict=True))
    return ClassificationResult(classifier_id, probabilities, meta.version, meta.dataset_hash)


def letterbox(frame: Frame, size: int) -> NDArray[np.float32]:
    """Preprocesado estándar de YOLO: escala sin deformar, rellena con gris 114, CHW en [0, 1]."""
    height, width = frame.shape[:2]
    scale = size / max(height, width)
    resized = cv2.resize(frame, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    top, left = (size - resized.shape[0]) // 2, (size - resized.shape[1]) // 2
    canvas[top:top + resized.shape[0], left:left + resized.shape[1]] = resized
    return np.ascontiguousarray(canvas.transpose(2, 0, 1), dtype=np.float32) / 255.0
