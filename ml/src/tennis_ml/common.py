"""Rutas y utilidades compartidas por las etapas del pipeline (todas relativas a ml/)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

PARAMS_FILE = Path("params.yaml")
RAW_DIR = Path("data/raw")
LABELS_CSV = Path("data/labels.csv")
POSES_DIR = Path("data/poses")
FEATURES_NPZ = Path("data/features.npz")
POSE_SEQ_DIR = Path("artifacts/pose_seq")
POSE_SEQ_EXPORT = Path("export/pose_seq")
YOLO_DATASET = Path("data/yolo")
YOLO_DIR = Path("artifacts/yolo")
YOLO_EXPORT = Path("export/yolo")
METRICS_DIR = Path("metrics")


def load_params(path: Path = PARAMS_FILE) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)  # type: ignore[no-any-return]


def md5_file(path: Path) -> str:
    """Mismo hash que DVC guarda en dvc.lock para un archivo: enlaza el modelo con sus datos exactos."""
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return f"md5:{digest.hexdigest()}"
