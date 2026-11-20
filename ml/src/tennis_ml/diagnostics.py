"""Diagnóstico del clasificador pose_seq: confusión crítica y calibración.

Se ejecuta sin re-entrenar:
    python -m tennis_ml diagnose --out metrics/diagnostic.json

Complementa `metrics/pose_seq.json`: separa el riesgo de smash→saque de la precisión global y
comprueba si el umbral recomendado está realmente calibrado.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import torch

from tennis_ml import common as c
from tennis_ml.evaluation import confidence_bins, critical_confusion, per_class_f1
from tennis_ml.export import calibrate_threshold_with_report
from tennis_ml.features import load
from tennis_ml.model import StrokeGRU
from tennis_ml.train import WEIGHTS_FILE, predict_proba


def diagnose(features_path: Path, train_dir: Path, metrics_path: Path | None = None,
             params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Analiza val y test del modelo ya exportado, sin depender de MLflow ni de DVC."""
    p = params or c.load_params()
    pose = p.get("pose_seq", {})
    classes, splits = load(features_path)
    model = StrokeGRU(splits["train"].x.shape[2], len(classes), pose.get("hidden", 128),
                      pose.get("layers", 2), pose.get("dropout", 0.2))
    model.load_state_dict(torch.load(train_dir / WEIGHTS_FILE, map_location="cpu"))
    model.eval()

    val_probs = predict_proba(model, splits["val"].x)
    calibration = calibrate_threshold_with_report(val_probs, splits["val"].y,
                                                  float(pose.get("target_precision", 0.95)))
    report: dict[str, Any] = {
        "classes": classes,
        "calibration": {
            "threshold": calibration.threshold,
            "target_precision": calibration.target_precision,
            "val_precision_at_threshold": round(calibration.achieved_precision, 4),
            "reached_target": calibration.reached_target,
            "used_fallback": calibration.used_fallback,
        },
    }
    for split in ("val", "test"):
        data = splits[split]
        if len(data.y) == 0:
            report[split] = {"test_size": 0}
            continue
        probs = val_probs if split == "val" else predict_proba(model, data.x)
        predicted = probs.argmax(axis=1)
        accepted = probs.max(axis=1) >= calibration.threshold
        report[split] = {
            "size": len(data.y),
            "per_class_f1": per_class_f1(data.y, predicted, classes),
            "critical_confusion": critical_confusion(data.y, predicted, classes),
            "confidence_bins": confidence_bins(probs, data.y),
            "accepted": {
                "coverage": round(float(accepted.mean()), 4),
                "precision": (round(float((predicted[accepted] == data.y[accepted]).mean()), 4)
                              if accepted.any() else None),
            },
        }
    if metrics_path is not None and metrics_path.exists():
        saved = json.loads(metrics_path.read_text(encoding="utf-8"))
        report["export_metrics"] = {k: saved.get(k) for k in
                                    ("test_macro_f1", "test_per_class", "critical_confusion",
                                     "threshold", "coverage_at_threshold", "accuracy_at_threshold",
                                     "threshold_reached_target")}
    return report


def print_report(report: dict[str, Any], out: Path | None = None) -> None:
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
