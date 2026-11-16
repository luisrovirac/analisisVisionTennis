"""Etapa export_pose_seq: evalúa en test, calibra el umbral, exporta a ONNX y registra el modelo.

Antes de dar el modelo por bueno, lo carga con el clasificador del worker y comprueba que da las mismas
probabilidades que PyTorch: si la exportación o el preprocesado divergieran, la etapa falla.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import torch
from sklearn.metrics import confusion_matrix, f1_score
from tennis_worker.pipeline.classification.features import FEATURE_SET
from tennis_worker.pipeline.classification.model import META_FILE, MODEL_FILE, OnnxModel, softmax

from tennis_ml.common import md5_file
from tennis_ml.datasets import read_labels
from tennis_ml.evaluation import baselines, low_support_classes, per_class_f1, split_summary
from tennis_ml.features import Split, load
from tennis_ml.model import StrokeGRU
from tennis_ml.train import RUN_FILE, WEIGHTS_FILE, predict_proba

REGISTERED_MODEL = "stroke-pose-seq"
OPSET = 17


def export(features_path: Path, train_dir: Path, params: dict[str, Any], out_dir: Path, metrics_path: Path,
           labels_path: Path | None = None) -> None:
    p = params["pose_seq"]
    run = json.loads((train_dir / RUN_FILE).read_text(encoding="utf-8"))
    classes, splits = load(features_path)
    model = StrokeGRU(splits["train"].x.shape[2], len(classes), p["hidden"], p["layers"], p["dropout"])
    model.load_state_dict(torch.load(train_dir / WEIGHTS_FILE))
    model.eval()
    threshold = calibrate_threshold(predict_proba(model, splits["val"].x), splits["val"].y, p["target_precision"])
    metrics = evaluate(predict_proba(model, splits["test"].x), splits["test"].y, classes, threshold)
    if metrics["test_size"]:
        metrics["baselines"] = baselines(splits, params["dataset"]["seed"])
    if labels_path is not None:
        metrics["splits"] = split_summary(read_labels(labels_path), classes)
    meta = {"classifier": "pose_seq", "version": params["version"] or _default_version(run["run_id"]),
            "classes": classes, "feature_set": FEATURE_SET, "seq_len": p["seq_len"],
            "recommended_min_confidence": threshold, "mlflow_run_id": run["run_id"],
            "dataset_hash": md5_file(features_path)}
    _write_onnx(model, p["seq_len"], splits["train"].x.shape[2], out_dir, meta)
    check_parity(model, out_dir, splits["test"] if len(splits["test"].y) else splits["val"])
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    _log_to_mlflow(run["run_id"], metrics, out_dir)


def calibrate_threshold(probs: np.ndarray, y: np.ndarray, target_precision: float) -> float:
    """Menor umbral cuya precisión sobre las predicciones aceptadas alcanza el objetivo en validación.
    Por debajo del umbral el worker responde UNSUPPORTED_STROKE en lugar de arriesgarse a equivocarse."""
    confidence, correct = probs.max(axis=1), probs.argmax(axis=1) == y
    for threshold in np.round(np.arange(0.3, 1.0, 0.01), 2):
        accepted = confidence >= threshold
        if accepted.any() and correct[accepted].mean() >= target_precision:
            return float(threshold)
    return 0.99


def evaluate(probs: np.ndarray, y: np.ndarray, classes: list[str], threshold: float) -> dict[str, Any]:
    if len(y) == 0:
        return {"test_size": 0}
    predicted, confidence = probs.argmax(axis=1), probs.max(axis=1)
    accepted = confidence >= threshold
    per_class = per_class_f1(y, predicted, classes)
    return {
        "test_size": len(y),
        "test_macro_f1": round(float(f1_score(y, predicted, average="macro", zero_division=0)), 4),
        "test_per_class": per_class,
        "test_low_support_classes": low_support_classes(per_class),
        "test_accuracy": round(float((predicted == y).mean()), 4),
        "threshold": threshold,
        "coverage_at_threshold": round(float(accepted.mean()), 4),
        "accuracy_at_threshold": round(float((predicted == y)[accepted].mean()), 4) if accepted.any() else None,
        "confusion_matrix": {"labels": classes,
                             "matrix": confusion_matrix(y, predicted, labels=range(len(classes))).tolist()},
    }


def _write_onnx(model: StrokeGRU, seq_len: int, features: int, out_dir: Path, meta: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(model, (torch.zeros(1, seq_len, features),), str(out_dir / MODEL_FILE),
                      input_names=["poses"], output_names=["logits"], opset_version=OPSET, dynamo=False)
    (out_dir / META_FILE).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def check_parity(model: StrokeGRU, out_dir: Path, split: Split, tolerance: float = 1e-4) -> None:
    onnx_model = OnnxModel(out_dir, "pose_seq")
    expected = predict_proba(model, split.x[:16])
    for sample, want in zip(split.x[:16], expected, strict=True):
        got = softmax(onnx_model.run(sample[np.newaxis])[0])
        if not np.allclose(got, want, atol=tolerance):
            raise AssertionError(f"ONNX y PyTorch divergen: {got} vs {want}")


def _default_version(run_id: str) -> str:
    return f"{datetime.now(timezone.utc):%Y%m%d}-{run_id[:8]}"


def _flat_metrics(metrics: dict[str, Any]) -> dict[str, float]:
    """Valores numéricos para MLflow, con nombres planos (test_f1_other, baseline_logreg_macro_f1, ...)."""
    flat = {k: v for k, v in metrics.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    for cls, v in metrics.get("test_per_class", {}).items():
        flat[f"test_f1_{cls}"] = v["f1"]
    for name, scores in metrics.get("baselines", {}).items():
        flat |= {f"baseline_{name}_{k}": s for k, s in scores.items()}
    for split, v in metrics.get("splits", {}).items():
        flat |= {f"{split}_players": v["players"], f"{split}_clips": v["clips"]}
    return flat


def _log_to_mlflow(run_id: str, metrics: dict[str, Any], out_dir: Path) -> None:
    with mlflow.start_run(run_id=run_id):
        mlflow.log_metrics(_flat_metrics(metrics))
        mlflow.log_dict(metrics, "metrics/pose_seq.json")
        mlflow.log_artifacts(str(out_dir), artifact_path="export")
        mlflow.register_model(f"runs:/{run_id}/export", REGISTERED_MODEL)
