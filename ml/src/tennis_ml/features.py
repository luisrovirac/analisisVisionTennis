"""Etapa features: poses JSON → tensores por partición, con la función de características del worker."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from tennis_worker.pipeline.classification.features import sequence_features
from tennis_worker.pipeline.pose.serialization import from_artifact

from tennis_ml.datasets import Sample

SPLITS = ("train", "val", "test")


@dataclass(frozen=True)
class Split:
    x: NDArray[np.float32]  # (N, seq_len, 99)
    y: NDArray[np.int64]  # (N,) índice en classes
    ids: NDArray[np.str_]


def build(samples: list[Sample], poses_dir: Path, classes: list[str], seq_len: int, out: Path) -> None:
    unknown = {s.label for s in samples} - set(classes)
    if unknown:
        raise ValueError(f"etiquetas fuera de params.classes: {sorted(unknown)}")
    arrays: dict[str, NDArray[np.generic]] = {"classes": np.array(classes)}
    for split in SPLITS:
        chosen = [s for s in samples if s.split == split]
        x = [_features(poses_dir / f"{s.id}.json", s.handedness, seq_len) for s in chosen]
        arrays[f"x_{split}"] = np.stack(x) if x else np.zeros((0, seq_len, 99), dtype=np.float32)
        arrays[f"y_{split}"] = np.array([classes.index(s.label) for s in chosen], dtype=np.int64)
        arrays[f"ids_{split}"] = np.array([s.id for s in chosen])
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, **arrays)  # type: ignore[arg-type]


def _features(path: Path, handedness: str, seq_len: int) -> NDArray[np.float32]:
    pose = from_artifact(json.loads(path.read_text(encoding="utf-8")))
    return sequence_features(pose, handedness, seq_len)  # type: ignore[arg-type]


def load(path: Path) -> tuple[list[str], dict[str, Split]]:
    with np.load(path) as data:
        splits = {s: Split(data[f"x_{s}"], data[f"y_{s}"], data[f"ids_{s}"]) for s in SPLITS}
        return [str(c) for c in data["classes"]], splits
