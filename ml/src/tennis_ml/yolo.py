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
from numpy.typing import NDArray
from tennis_worker.contracts import JobMessage
from tennis_worker.pipeline.classification import ClassificationContext, YoloFrameClassifier
from tennis_worker.pipeline.classification.model import META_FILE, MODEL_FILE, OnnxModel
from tennis_worker.pipeline.pose.serialization import from_artifact
from tennis_worker.pipeline.video import OpenCVVideoReader

from tennis_ml.common import md5_file
from tennis_ml.datasets import Sample
from tennis_ml.export import calibrate_threshold_with_report, evaluate

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
    # simplify=False evita que Ultralytics instale onnxslim en caliente: su dependencia exige
    # numpy>=2, que rompe onnxruntime 1.18 (numpy<2) y al resto del worker (ver pyproject.toml).
    onnx_path = Path(model.export(format="onnx", imgsz=p["imgsz"], opset=17, simplify=False))
    export_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(onnx_path, export_dir / MODEL_FILE)
    return export_dir


def write_meta(export_dir: Path, params: dict[str, Any], classes: list[str], labels_csv: Path, version: str) -> None:
    meta = {"classifier": "yolo", "version": version, "classes": classes, "imgsz": params["yolo"]["imgsz"],
            "frames": params["yolo"]["clip_frames"], "dataset_hash": md5_file(labels_csv)}
    (export_dir / META_FILE).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def _clip_probabilities(model: OnnxModel, samples: list[Sample], raw_dir: Path, poses_dir: Path,
                        classes: list[str], split: str) -> tuple[NDArray[np.float64], NDArray[np.int64]]:
    """Puntuaciones del clasificador del worker por clip, en el formato que espera `export.evaluate`."""
    classifier = YoloFrameClassifier(model, OpenCVVideoReader(max_seconds=60))
    known = {label: index for index, label in enumerate(classes)}
    probs, truth = [], []
    for sample in (s for s in samples if s.split == split):
        pose = from_artifact(json.loads((poses_dir / f"{sample.id}.json").read_text(encoding="utf-8")))
        job = JobMessage(schema_version="1.1", analysis_id=sample.id, video_key="", handedness="right",
                         camera_view="side")
        label, confidence = classifier.classify(ClassificationContext(pose, raw_dir / sample.source, job)).top
        row = np.zeros(len(classes), dtype=np.float64)
        if label in known:
            row[known[label]] = confidence  # sigmoides independientes: solo hace falta la ganadora
        probs.append(row)
        truth.append(known.get(sample.label, -1))
    return np.stack(probs) if probs else np.zeros((0, len(classes))), np.array(truth, dtype=np.int64)


def evaluate_clips(export_dir: Path, samples: list[Sample], raw_dir: Path, poses_dir: Path,
                   classes: list[str], target_precision: float = 0.95) -> dict[str, Any]:
    """Evalúa YOLO igual que `pose_seq`: F1 por clase, confusión crítica y umbral calibrado en val.

    Las puntuaciones son las del `YoloFrameClassifier` que usa el worker en producción, así que
    `STROKE_MIN_CONFIDENCE` significa lo mismo aquí que en el modelo de pose.
    """
    model = OnnxModel(export_dir, "yolo")
    val_probs, val_y = _clip_probabilities(model, samples, raw_dir, poses_dir, classes, "val")
    calibration = calibrate_threshold_with_report(val_probs, val_y, target_precision)
    test_probs, test_y = _clip_probabilities(model, samples, raw_dir, poses_dir, classes, "test")
    if not len(test_y):
        return {"test_size": 0}
    metrics = evaluate(test_probs, test_y, classes, calibration.threshold)
    metrics.update({
        "threshold_target_precision": calibration.target_precision,
        "threshold_reached_target": calibration.reached_target,
        "threshold_used_fallback": calibration.used_fallback,
        "val_precision_at_threshold": round(calibration.achieved_precision, 4),
    })
    return metrics
