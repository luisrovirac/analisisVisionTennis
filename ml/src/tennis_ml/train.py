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
import torch.nn.functional as F
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
        mlflow.set_tag("variant", _variant_tag(p))
        best_score, best_macro = _fit(model, splits, p, classes)
        mlflow.log_metrics({"val_macro_f1_best": best_macro, "val_selection_score_best": best_score})
    out_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), out_dir / WEIGHTS_FILE)
    (out_dir / RUN_FILE).write_text(json.dumps({"run_id": run.info.run_id, "classes": classes}), encoding="utf-8")
    return str(run.info.run_id)


def _fit(model: StrokeGRU, splits: dict[str, Split], p: dict[str, Any], classes: list[str]) -> tuple[float, float]:
    train_split, val = splits["train"], splits["val"]
    loader = DataLoader(TensorDataset(torch.from_numpy(train_split.x), torch.from_numpy(train_split.y)),
                        batch_size=p["batch_size"], shuffle=True)
    optimizer = torch.optim.Adam(model.parameters(), lr=p["lr"])
    other_index = classes.index("other") if "other" in classes else None
    weights = _class_weights(train_split.y, model.head[-1].out_features,
                             boost=float(p.get("other_weight_boost", 1.0)), other_index=other_index)
    loss_fn = nn.CrossEntropyLoss(weight=weights)
    best_score, best_macro, best_state = -1.0, -1.0, model.state_dict()
    for epoch in range(p["epochs"]):
        model.train()
        total = 0.0
        for x, y in loader:
            optimizer.zero_grad()
            loss = loss_fn(model(_augment(x, p)), y)
            loss.backward()
            optimizer.step()
            total += float(loss) * len(y)
        val_f1 = macro_f1(model, val)
        score = _selection_score(model, val, p, classes, val_f1)
        mlflow.log_metrics({"train_loss": total / max(len(train_split.y), 1), "val_macro_f1": val_f1,
                            "val_selection_score": score}, step=epoch)
        if score > best_score:  # early model selection: mejor época según la métrica elegida
            best_score, best_macro = score, val_f1
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    return best_score, best_macro


def _augment(x: torch.Tensor, p: dict[str, Any]) -> torch.Tensor:
    """Aumentación SOLO de entrenamiento: ruido gaussiano y time-warping de la secuencia de pose."""
    noise_std = float(p.get("noise_std", 0.0))
    time_warp = float(p.get("time_warp", 0.0))
    if noise_std > 0:
        noise = torch.randn_like(x)
        noise.mul_(noise_std)
        x = x + noise
    if time_warp <= 0:
        return x
    warped = []
    for sequence in x:  # T varía por muestra; F.interpolate exige un tamaño común por lote
        length = sequence.shape[0]
        factor = 1.0 + (torch.rand(1).item() * 2 - 1) * time_warp
        target = max(2, min(length * 4, round(length * factor)))
        if target == length:
            warped.append(sequence)
            continue
        signal = sequence.transpose(0, 1).unsqueeze(0)  # (1, F, T)
        stretched = F.interpolate(signal, size=target, mode="linear", align_corners=False)
        restored = F.interpolate(stretched, size=length, mode="linear", align_corners=False)
        warped.append(restored.squeeze(0).transpose(0, 1))
    return torch.stack(warped)


def _class_weights(y: np.ndarray, classes: int, boost: float = 1.0,
                   other_index: int | None = None) -> torch.Tensor:
    """Pondera las clases minoritarias (THETIS tiene 3 subtipos de saque y 1 de smash).

    El peso base es inversamente proporcional a la frecuencia; `boost` multiplica solo `other`
    para poder barrer su influencia sin reescribir la fórmula.
    """
    counts = np.bincount(y, minlength=classes).astype(np.float32)
    weights = np.where(counts > 0, counts.sum() / np.maximum(counts, 1) / classes, 0.0)
    if other_index is not None and 0 <= other_index < len(weights):
        weights[other_index] *= boost
    return torch.from_numpy(weights.astype(np.float32))


def _selection_score(model: StrokeGRU, val: Split, p: dict[str, Any], classes: list[str],
                     macro: float) -> float:
    """Permite penalizar específicamente los errores `other → serve` al elegir la mejor época."""
    penalty = float(p.get("other_serve_penalty", 0.0))
    if penalty <= 0 or "other" not in classes or "serve" not in classes or len(val.y) == 0:
        return macro
    other_index, serve_index = classes.index("other"), classes.index("serve")
    wrong = val.y == other_index
    if not wrong.any():
        return macro
    rate = float((predict_proba(model, val.x[wrong]).argmax(axis=1) == serve_index).mean())
    return macro - penalty * rate


def _variant_tag(p: dict[str, Any]) -> str:
    keys = ("other_weight_boost", "noise_std", "time_warp", "other_serve_penalty")
    return " ".join(f"{k}={p.get(k, 1.0 if k == 'other_weight_boost' else 0.0)}" for k in keys)


def predict_proba(model: StrokeGRU, x: np.ndarray) -> np.ndarray:
    model.eval()
    with torch.no_grad():
        return torch.softmax(model(torch.from_numpy(x)), dim=1).numpy()  # type: ignore[no-any-return]


def macro_f1(model: StrokeGRU, split: Split) -> float:
    if len(split.y) == 0:
        return 0.0
    predicted = predict_proba(model, split.x).argmax(axis=1)
    return float(f1_score(split.y, predicted, average="macro", zero_division=0))
