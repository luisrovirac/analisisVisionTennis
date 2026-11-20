"""La evaluación de YOLO aplica las mismas compuertas que pose_seq, sin depender de ultralytics."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tennis_ml import yolo
from tennis_ml.datasets import Sample

CLASSES = ["forehand", "backhand", "serve", "volley", "other"]


def test_yolo_evaluation_uses_worker_scores_and_calibration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    poses = tmp_path / "poses"
    poses.mkdir()
    samples = [
        Sample(f"v{i}", "x", label, f"p{i}", split=split)
        for i, (split, label) in enumerate([
            ("val", "other"), ("val", "serve"), ("val", "serve"),
            ("test", "other"), ("test", "serve"), ("test", "forehand"),
        ])
    ]
    for sample in samples:
        (poses / f"{sample.id}.json").write_text("{}", encoding="utf-8")

    # Predicación fija: en val el smash se confunde con saque; en test acierta.
    scores = {"v0": ("serve", 0.99), "v1": ("serve", 0.99), "v2": ("serve", 0.9),
              "v3": ("other", 0.8), "v4": ("serve", 0.8), "v5": ("forehand", 0.7)}

    class FakeClassifier:
        def __init__(self, model: Any, reader: Any) -> None:
            pass

        def classify(self, ctx: Any) -> Any:
            label, confidence = scores[ctx.job.analysis_id]
            return SimpleNamespace(top=(label, confidence))

    monkeypatch.setattr(yolo, "OnnxModel", lambda *args: SimpleNamespace())
    monkeypatch.setattr(yolo, "YoloFrameClassifier", FakeClassifier)
    monkeypatch.setattr(yolo, "from_artifact", lambda _data: SimpleNamespace(frame_count=2))

    metrics = yolo.evaluate_clips(tmp_path / "export", samples, tmp_path, poses, CLASSES,
                                  target_precision=0.95)
    assert metrics["test_size"] == 3
    assert set(metrics["test_per_class"]) == set(CLASSES)
    assert metrics["critical_confusion"]["count"] == 0  # el smash de test se clasificó bien
    assert "threshold_reached_target" in metrics
    assert "val_precision_at_threshold" in metrics
    assert 0.0 <= metrics["coverage_at_threshold"] <= 1.0


def test_onnx_export_keeps_numpy_1_compatibility(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ultralytics intenta autoinstalar onnxslim (numpy>=2) si simplify=True: rompe onnxruntime."""
    fake_onnx = tmp_path / "best.onnx"
    fake_onnx.write_bytes(b"onnx")
    calls: dict[str, Any] = {}

    class FakeYOLO:
        def __init__(self, base: str) -> None:
            calls["base"] = base

        def train(self, **kwargs: Any) -> None:
            calls["train"] = kwargs

        def export(self, **kwargs: Any) -> str:
            calls["export"] = kwargs
            return str(fake_onnx)

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYOLO))
    params = {"yolo": {"base": "yolo11n.pt", "epochs": 1, "imgsz": 640, "device": "cpu"},
              "dataset": {"seed": 42}}
    out = yolo.train_and_export(tmp_path / "dataset", params, tmp_path / "train", tmp_path / "export")
    assert (out / "model.onnx").read_bytes() == b"onnx"
    assert calls["export"]["simplify"] is False
