"""Pipeline de punta a punta sobre el dataset sintético: prepare → features → train → export, y el modelo
exportado se carga con el clasificador del worker (la misma clase que usa producción)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import pytest
import torch
from tennis_worker.contracts import JobMessage
from tennis_worker.pipeline.classification import ClassificationContext, PoseSequenceClassifier
from tennis_worker.pipeline.classification.model import OnnxModel
from tennis_worker.pipeline.pose.serialization import from_artifact

from tennis_ml.datasets import Sample, prepare, read_labels, split_by_player, write_labels
from tennis_ml.evaluation import critical_confusion, low_support_classes, per_class_f1, split_summary
from tennis_ml.export import calibrate_threshold, calibrate_threshold_with_report, export
from tennis_ml.features import build, load
from tennis_ml.toy import make_toy
from tennis_ml.train import _augment, _class_weights, train

CLASSES = ["forehand", "backhand", "serve", "volley", "other"]


def params(epochs: int = 15) -> dict[str, Any]:
    return {
        "version": "test-1", "classes": CLASSES,
        "dataset": {"name": "toy", "seed": 1, "splits": {"train": 0.6, "val": 0.2, "test": 0.2}},
        "pose_seq": {"seq_len": 16, "hidden": 32, "layers": 1, "dropout": 0.0, "epochs": epochs,
                     "batch_size": 16, "lr": 0.01, "target_precision": 0.9},
    }


@pytest.fixture(autouse=True)
def local_mlflow(tmp_path: Path) -> None:
    mlflow.set_tracking_uri((tmp_path / "mlruns").as_uri())


def test_split_is_by_player_and_deterministic() -> None:
    players = [f"p{i}" for i in range(10)]
    first = split_by_player(players, seed=3, fractions={"train": 0.7, "val": 0.2, "test": 0.1})
    assert first == split_by_player(reversed(players), seed=3, fractions={"train": 0.7, "val": 0.2, "test": 0.1})
    assert sorted(first.values()).count("train") == 7 and set(first) == set(players)


def test_thetis_layout_is_mapped_to_product_classes(tmp_path: Path) -> None:
    for action, player in [("forehand_flat", 1), ("smash", 2), ("kick_service", 3), ("backhand2hands", 4)]:
        folder = tmp_path / "VIDEO_RGB" / action
        folder.mkdir(parents=True)
        (folder / f"p{player}_{action}_s1.avi").write_bytes(b"")
    samples = prepare("thetis", tmp_path, seed=0, fractions={"train": 1.0})
    assert {s.label for s in samples} == {"forehand", "other", "serve", "backhand"}
    assert {s.player for s in samples} == {"thetis-1", "thetis-2", "thetis-3", "thetis-4"}


def test_calibrated_threshold_reaches_target_precision() -> None:
    probs = np.array([[0.95, 0.05], [0.9, 0.1], [0.6, 0.4], [0.55, 0.45]])
    y = np.array([0, 0, 1, 0])  # las dos predicciones dudosas: una mal
    assert calibrate_threshold(probs, y, target_precision=1.0) == pytest.approx(0.61)


def test_calibration_reports_fallback_when_target_is_unreachable() -> None:
    probs = np.array([[0.99, 0.01]] * 10)  # sobreconfiado: 9 aciertos de 10, nunca llega al 95 %
    y = np.array([0] * 9 + [1])
    report = calibrate_threshold_with_report(probs, y, target_precision=0.95)
    assert report.threshold == pytest.approx(0.99)
    assert report.used_fallback and not report.reached_target
    assert report.achieved_precision == pytest.approx(0.9)


def test_critical_confusion_counts_smash_as_serve() -> None:
    y = np.array([4, 4, 3, 4])
    predicted = np.array([2, 4, 3, 0])
    classes = ["forehand", "backhand", "serve", "volley", "other"]
    result = critical_confusion(y, predicted, classes)
    assert result["count"] == 1 and result["support"] == 3
    assert result["rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert critical_confusion(y, predicted, classes[:-1])["available"] is False


def test_class_weights_boost_only_other() -> None:
    y = np.array([0] * 10 + [1] * 10 + [4] * 5)
    base = _class_weights(y, 5)
    boosted = _class_weights(y, 5, boost=3.0, other_index=4)
    assert boosted[4] == pytest.approx(3 * base[4])
    assert torch.allclose(base[:4], boosted[:4])


def test_augmentation_is_shape_preserving_and_opt_in() -> None:
    torch.manual_seed(0)
    x = torch.rand(4, 16, 99)
    assert torch.equal(_augment(x, {}), x)
    noisy = _augment(x, {"noise_std": 0.05, "time_warp": 0.1})
    assert noisy.shape == x.shape
    assert not torch.equal(noisy, x)


def test_per_class_f1_and_split_summary() -> None:
    y, predicted = np.array([0, 0, 1, 1, 2]), np.array([0, 0, 1, 0, 2])
    per_class = per_class_f1(y, predicted, ["a", "b", "c", "d"])
    assert per_class["a"] == {"f1": 0.8, "support": 2} and per_class["d"] == {"f1": 0.0, "support": 0}
    assert low_support_classes(per_class, minimum=2) == ["c", "d"]
    samples = [Sample("1", "x", "a", "p1", split="train"), Sample("2", "x", "b", "p1", split="train"),
               Sample("3", "x", "a", "p2", split="test")]
    summary = split_summary(samples, ["a", "b"])
    assert summary["train"] == {"players": 1, "clips": 2, "classes": {"a": 1, "b": 1}}
    assert summary["test"]["classes"] == {"a": 1, "b": 0} and summary["val"]["clips"] == 0


def test_toy_pipeline_trains_exports_and_serves_with_worker_classifier(tmp_path: Path) -> None:
    raw, poses = tmp_path / "raw", tmp_path / "raw"  # en toy, las poses ya son la fuente
    make_toy(raw, players=10, clips_per_class=3)
    p = params()
    labels = tmp_path / "labels.csv"
    write_labels(prepare("toy", raw, 1, p["dataset"]["splits"]), labels)
    samples = read_labels(labels)
    for s in samples:  # extract_poses en toy = copiar; aquí se leen directamente
        (poses / f"{s.id}.json").write_bytes((raw / s.source).read_bytes())
    features = tmp_path / "features.npz"
    build(samples, poses, CLASSES, p["pose_seq"]["seq_len"], features)
    classes, splits = load(features)
    assert classes == CLASSES and splits["train"].x.shape[1:] == (16, 99)

    train(features, p, tmp_path / "train")
    export_dir, metrics_path = tmp_path / "export", tmp_path / "metrics.json"
    export(features, tmp_path / "train", p, export_dir, metrics_path, labels)  # incluye la comprobación de paridad

    metrics = json.loads(metrics_path.read_text())
    assert metrics["test_macro_f1"] > 0.8  # las trayectorias sintéticas son fácilmente separables
    assert set(metrics["test_per_class"]) == set(CLASSES)
    assert sum(v["support"] for v in metrics["test_per_class"].values()) == metrics["test_size"]
    assert metrics["splits"]["test"]["clips"] == metrics["test_size"]
    assert sum(v["players"] for v in metrics["splits"].values()) == 10
    assert metrics["baselines"]["majority"]["macro_f1"] < metrics["test_macro_f1"]
    assert "logreg" in metrics["baselines"]
    assert metrics["threshold_target_precision"] == pytest.approx(0.9)
    assert isinstance(metrics["threshold_reached_target"], bool)
    assert 0.0 <= metrics["val_precision_at_threshold"] <= 1.0
    assert metrics["critical_confusion"]["source"] == "other"
    meta = json.loads((export_dir / "meta.json").read_text())
    assert meta["version"] == "test-1" and meta["dataset_hash"].startswith("md5:")

    classifier = PoseSequenceClassifier(OnnxModel(export_dir, "pose_seq"))
    sample = next(s for s in samples if s.split == "test" and s.label == "serve")
    pose = from_artifact(json.loads((poses / f"{sample.id}.json").read_text()))
    job = JobMessage(schema_version="1.1", analysis_id="a", video_key="v", handedness="right", camera_view="side")
    assert classifier.classify(ClassificationContext(pose, Path("unused"), job)).top[0] == "serve"
