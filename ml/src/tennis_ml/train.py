"""Etapa train_pose_seq: entrena StrokeGRU y registra el experimento en MLflow.

Sin MLFLOW_TRACKING_URI, MLflow guarda en ./mlruns (local). En docker-compose apunta al servidor.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import torch
from sklearn.metrics import f1_score
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from tennis_ml.common import md5_file
from tennis_ml.features import Split, load
from tennis_ml.model import StrokeGRU

logger = logging.getLogger(__name__)

EXPERIMENT = "stroke-classifier"
RUN_FILE = "run.json"
WEIGHTS_FILE = "model.pt"


def train(features_path: Path, params: dict[str, Any], out_dir: Path) -> str:
    """Devuelve el run_id de MLflow; export lo retoma para registrar métricas y artefactos en el mismo run."""
    p = params["pose_seq"]
    torch.manual_seed(params["dataset"]["seed"])
    classes, splits = load(features_path)
    model = StrokeGRU(splits["train"].x.shape[2], len(classes), p["hidden"], p["layers"], p["dropout"])
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name="pose_seq") as run:
        mlflow.log_params({f"pose_seq.{k}": v for k, v in p.items()} | {
            "dataset": params["dataset"]["name"], "classes": ",".join(classes),
            "dataset_hash": md5_file(features_path), "train_size": len(splits["train"].y)})
        best = _fit(model, splits, p)
        mlflow.log_metric("val_macro_f1_best", best)
    out_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), out_dir / WEIGHTS_FILE)
    (out_dir / RUN_FILE).write_text(json.dumps({"run_id": run.info.run_id, "classes": classes}), encoding="utf-8")
    return str(run.info.run_id)


def _fit(model: StrokeGRU, splits: dict[str, Split], p: dict[str, Any]) -> float:
    train_split, val = splits["train"], splits["val"]
    loader = DataLoader(TensorDataset(torch.from_numpy(train_split.x), torch.from_numpy(train_split.y)),
                        batch_size=p["batch_size"], shuffle=True)
    optimizer = torch.optim.Adam(model.parameters(), lr=p["lr"])
    loss_fn = nn.CrossEntropyLoss(weight=_class_weights(train_split.y, model.head[-1].out_features))
    best_f1, best_state = -1.0, model.state_dict()
    for epoch in range(p["epochs"]):
        model.train()
        total = 0.0
        for x, y in loader:
            optimizer.zero_grad()
            loss = loss_fn(model(x), y)
            loss.backward()
            optimizer.step()
            total += float(loss) * len(y)
        val_f1 = macro_f1(model, val)
        mlflow.log_metrics({"train_loss": total / max(len(train_split.y), 1), "val_macro_f1": val_f1}, step=epoch)
        if val_f1 > best_f1:  # early model selection: se queda con la mejor época en validación
            best_f1, best_state = val_f1, {k: v.clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    return best_f1


def _class_weights(y: np.ndarray, classes: int) -> torch.Tensor:
    """Pondera las clases minoritarias (THETIS tiene 3 subtipos de saque y 1 de smash)."""
    counts = np.bincount(y, minlength=classes).astype(np.float32)
    weights = np.where(counts > 0, counts.sum() / np.maximum(counts, 1) / classes, 0.0)
    return torch.from_numpy(weights.astype(np.float32))


def predict_proba(model: StrokeGRU, x: np.ndarray) -> np.ndarray:
    model.eval()
    with torch.no_grad():
        return torch.softmax(model(torch.from_numpy(x)), dim=1).numpy()  # type: ignore[no-any-return]


def macro_f1(model: StrokeGRU, split: Split) -> float:
    if len(split.y) == 0:
        return 0.0
    predicted = predict_proba(model, split.x).argmax(axis=1)
    return float(f1_score(split.y, predicted, average="macro", zero_division=0))
