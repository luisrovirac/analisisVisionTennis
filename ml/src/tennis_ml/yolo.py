"""Ruta YOLO: detector cuyas clases son los golpes (enfoque de HL-TAR-YOLO / TAR-Det).

THETIS no trae cajas, así que se generan automáticamente: la caja de cada fotograma es la del
esqueleto de MediaPipe (ya extraído en data/poses) con un margen, etiquetada con la clase del clip.
La evaluación final es por clip y usa el clasificador del worker (YoloFrameClassifier): se mide
exactamente lo que verá producción.

Necesita el extra [yolo] (ultralytics, AGPL-3.0) y en la práctica una GPU para entrenar.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml
from sklearn.metrics import f1_score
from tennis_worker.contracts import JobMessage
from tennis_worker.pipeline.classification import ClassificationContext, YoloFrameClassifier
from tennis_worker.pipeline.classification.model import META_FILE, MODEL_FILE, OnnxModel
from tennis_worker.pipeline.pose.serialization import from_artifact
from tennis_worker.pipeline.video import OpenCVVideoReader

from tennis_ml.common import md5_file
from tennis_ml.datasets import Sample

BOX_MARGIN = 0.15
SPLIT_DIRS = {"train": "train", "val": "val", "test": "test"}


def build_dataset(samples: list[Sample], raw_dir: Path, poses_dir: Path, classes: list[str],
                  frames_per_clip: int, out_dir: Path) -> None:
    for split in SPLIT_DIRS.values():
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)
    for sample in samples:
        pose = from_artifact(json.loads((poses_dir / f"{sample.id}.json").read_text(encoding="utf-8")))
        detected = np.flatnonzero(pose.detected)
        if len(detected) == 0:
            continue
        chosen = set(detected[np.linspace(0, len(detected) - 1, frames_per_clip).round().astype(int)].tolist())
        _write_frames(raw_dir / sample.source, pose.image, chosen, classes.index(sample.label),
                      out_dir, SPLIT_DIRS[sample.split], sample.id)
    names = dict(enumerate(classes))
    config = {"path": str(out_dir.resolve()), "train": "images/train", "val": "images/val", "names": names}
    (out_dir / "dataset.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")


def _write_frames(video: Path, landmarks: np.ndarray, chosen: set[int], class_id: int,
                  out_dir: Path, split: str, stem: str) -> None:
    capture = cv2.VideoCapture(str(video))
    try:
        index = 0
        while chosen:
            ok, frame = capture.read()
            if not ok:
                return
            if index in chosen:
                chosen.discard(index)
                cv2.imwrite(str(out_dir / "images" / split / f"{stem}_{index}.jpg"), frame)
                label = f"{class_id} " + " ".join(f"{v:.5f}" for v in _box(landmarks[index]))
                (out_dir / "labels" / split / f"{stem}_{index}.txt").write_text(label + "\n", encoding="utf-8")
            index += 1
    finally:
        capture.release()


def _box(points: np.ndarray) -> tuple[float, float, float, float]:
    """Caja YOLO normalizada (cx, cy, w, h) que envuelve el esqueleto, con margen."""
    xs, ys = points[:, 0], points[:, 1]
    w, h = (xs.max() - xs.min()) * (1 + 2 * BOX_MARGIN), (ys.max() - ys.min()) * (1 + 2 * BOX_MARGIN)
    cx, cy = (xs.max() + xs.min()) / 2, (ys.max() + ys.min()) / 2
    return float(np.clip(cx, 0, 1)), float(np.clip(cy, 0, 1)), float(min(w, 1.0)), float(min(h, 1.0))


def train_and_export(dataset_dir: Path, params: dict[str, Any], train_dir: Path, export_dir: Path) -> Path:
    from ultralytics import YOLO  # extra opcional: solo se importa en esta etapa

    p = params["yolo"]
    model = YOLO(p["base"])
    model.train(data=str(dataset_dir / "dataset.yaml"), epochs=p["epochs"], imgsz=p["imgsz"], device=p["device"],
                project=str(train_dir), name="run", exist_ok=True, seed=params["dataset"]["seed"])
    onnx_path = Path(model.export(format="onnx", imgsz=p["imgsz"], opset=17))
    export_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(onnx_path, export_dir / MODEL_FILE)
    return export_dir


def write_meta(export_dir: Path, params: dict[str, Any], classes: list[str], labels_csv: Path, version: str) -> None:
    meta = {"classifier": "yolo", "version": version, "classes": classes, "imgsz": params["yolo"]["imgsz"],
            "frames": params["yolo"]["clip_frames"], "dataset_hash": md5_file(labels_csv)}
    (export_dir / META_FILE).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def evaluate_clips(export_dir: Path, samples: list[Sample], raw_dir: Path, poses_dir: Path) -> dict[str, Any]:
    """Precisión por clip en test con el mismo clasificador que usa el worker en producción."""
    model = OnnxModel(export_dir, "yolo")
    classifier = YoloFrameClassifier(model, OpenCVVideoReader(max_seconds=60))
    truth, predicted = [], []
    for sample in (s for s in samples if s.split == "test"):
        pose = from_artifact(json.loads((poses_dir / f"{sample.id}.json").read_text(encoding="utf-8")))
        job = JobMessage(schema_version="1.1", analysis_id=sample.id, video_key="", handedness="right",
                         camera_view="side")
        result = classifier.classify(ClassificationContext(pose, raw_dir / sample.source, job))
        truth.append(sample.label)
        predicted.append(result.top[0])
    if not truth:
        return {"test_size": 0}
    return {"test_size": len(truth), "test_accuracy": float(np.mean(np.array(truth) == np.array(predicted))),
            "test_macro_f1": float(f1_score(truth, predicted, average="macro", zero_division=0))}
