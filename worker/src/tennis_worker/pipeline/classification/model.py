"""Artefacto de un modelo exportado: `model.onnx` + `meta.json`, tal como lo genera ml/ (etapa export)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

MODEL_FILE = "model.onnx"
META_FILE = "meta.json"


class ModelMeta(BaseModel):
    """meta.json. Une el modelo con el experimento (MLflow) y los datos (DVC) que lo produjeron."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    classifier: str
    version: str
    classes: list[str] = Field(min_length=2)
    feature_set: str | None = None  # solo pose_seq
    seq_len: int | None = Field(default=None, ge=2)  # solo pose_seq
    imgsz: int | None = Field(default=None, ge=32)  # solo yolo
    frames: int | None = Field(default=None, ge=1)  # solo yolo: fotogramas muestreados por clip
    recommended_min_confidence: float | None = None  # calibrado en validación (informativo)
    mlflow_run_id: str | None = None
    dataset_hash: str | None = None


class OnnxModel:
    """Sesión ONNX en CPU. Se carga una vez al arrancar el worker (fail fast si falta o está corrupto)."""

    def __init__(self, directory: Path, expected_classifier: str) -> None:
        meta_path, model_path = directory / META_FILE, directory / MODEL_FILE
        if not meta_path.exists() or not model_path.exists():
            raise FileNotFoundError(f"faltan {MODEL_FILE}/{META_FILE} en {directory}")
        self.meta = ModelMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
        if self.meta.classifier != expected_classifier:
            raise ValueError(f"{directory} contiene un modelo '{self.meta.classifier}', no '{expected_classifier}'")
        self._session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name

    def run(self, batch: NDArray[np.float32]) -> NDArray[np.float32]:
        outputs: Sequence[Any] = self._session.run(None, {self._input: batch})
        return np.asarray(outputs[0], dtype=np.float32)


def softmax(logits: NDArray[np.float32]) -> NDArray[np.float64]:
    shifted = logits.astype(np.float64) - logits.max()
    exp = np.exp(shifted)
    return exp / exp.sum()  # type: ignore[no-any-return]
