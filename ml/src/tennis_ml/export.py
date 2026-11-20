"""Etapa export_pose_seq: evalúa en test, calibra el umbral, exporta a ONNX y registra el modelo.

Antes de dar el modelo por bueno, lo carga con el clasificador del worker y comprueba que da las mismas
probabilidades que PyTorch: si la exportación o el preprocesado divergieran, la etapa falla.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
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
from tennis_ml.evaluation import baselines, critical_confusion, low_support_classes, per_class_f1, split_summary
from tennis_ml.features import Split, load
from tennis_ml.model import StrokeGRU
from tennis_ml.train import RUN_FILE, WEIGHTS_FILE, predict_proba

REGISTERED_MODEL = "stroke-pose-seq"
OPSET = 17
# El modelo puede saturar el softmax: 0.99 era demasiado bajo y forzaba el fallback.
THRESHOLD_GRID = tuple(float(v) for v in (
    *np.round(np.arange(0.3, 1.0, 0.01), 2), 0.995, 0.999, 0.9992, 0.9995, 0.9997, 0.9999))
FALLBACK_THRESHOLD = 0.9999
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThresholdCalibration:
    """Resultado de calibrar el umbral: hace explícito cuándo no se alcanzó la precisión objetivo."""

    threshold: float
    target_precision: float
    achieved_precision: float
    reached_target: bool
    used_fallback: bool


def export(features_path: Path, train_dir: Path, params: dict[str, Any], out_dir: Path, metrics_path: Path,
           labels_path: Path | None = None) -> None:
    p = params["pose_seq"]
    run = json.loads((train_dir / RUN_FILE).read_text(encoding="utf-8"))
    classes, splits = load(features_path)
    model = StrokeGRU(splits["train"].x.shape[2], len(classes), p["hidden"], p["layers"], p["dropout"])
    model.load_state_dict(torch.load(train_dir / WEIGHTS_FILE))
    model.eval()
    calibration = calibrate_threshold_with_report(
        predict_proba(model, splits["val"].x), splits["val"].y, p["target_precision"])
    threshold = calibration.threshold
    if not calibration.reached_target:
        logger.warning("umbral NO calibrado: objetivo %.2f, precisión en validación %.4f, umbral %.2f%s",
                       calibration.target_precision, calibration.achieved_precision, threshold,
                       " (fallback)" if calibration.used_fallback else "")
    metrics = evaluate(predict_proba(model, splits["test"].x), splits["test"].y, classes, threshold)
    metrics.update({
        "threshold_target_precision": calibration.target_precision,
        "threshold_reached_target": calibration.reached_target,
        "threshold_used_fallback": calibration.used_fallback,
        "val_precision_at_threshold": round(calibration.achieved_precision, 4),
    })
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
    """Umbral mínimo que alcanza la precisión objetivo; compatible con llamadas antiguas."""
    return calibrate_threshold_with_report(probs, y, target_precision).threshold


def calibrate_threshold_with_report(probs: np.ndarray, y: np.ndarray,
                                    target_precision: float) -> ThresholdCalibration:
    """Calibra el umbral y explica si tuvo que recurrir al fallback.

    Por debajo del umbral el worker responde UNSUPPORTED_STROKE en lugar de arriesgarse a equivocarse.
    Si ningún umbral alcanza `target_precision` en validación, se devuelve 0.99 pero se marca
    `reached_target=False`: el modelo está sobreconfiado y ese número NO es una garantía.
    """
    confidence, correct = probs.max(axis=1), probs.argmax(axis=1) == y
    best: ThresholdCalibration | None = None
    for threshold in THRESHOLD_GRID:
        accepted = confidence >= threshold
        if not accepted.any():
            continue
        precision = float(correct[accepted].mean())
        candidate = ThresholdCalibration(float(threshold), target_precision, precision,
                                         precision >= target_precision, False)
        if candidate.reached_target:
            return candidate
        # En empate se prefiere el umbral más alto: es el más selectivo con la misma precisión.
        if best is None or candidate.achieved_precision >= best.achieved_precision:
            best = candidate
    if best is None:  # ninguna predicción alcanza siquiera el umbral más bajo
        return ThresholdCalibration(FALLBACK_THRESHOLD, target_precision, 0.0, False, True)
    # No se alcanzó el objetivo: se devuelve el mejor umbral disponible, pero marcado.
    return ThresholdCalibration(best.threshold, target_precision, best.achieved_precision, False, True)


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
        "critical_confusion": critical_confusion(y, predicted, classes),
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
    flat["threshold_reached_target"] = int(bool(metrics.get("threshold_reached_target")))
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
