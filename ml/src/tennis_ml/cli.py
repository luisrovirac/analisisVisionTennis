"""Punto de entrada de las etapas: `python -m tennis_ml <etapa>`. dvc.yaml llama a estas órdenes."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable
from pathlib import Path

from tennis_ml import common as c
from tennis_ml.datasets import prepare, read_labels, write_labels


def _prepare() -> None:
    p = c.load_params()
    d = p["dataset"]
    write_labels(prepare(d["name"], c.RAW_DIR / d["name"], d["seed"], d["splits"]), c.LABELS_CSV)


def _extract() -> None:
    from tennis_ml.extract import extract_all

    p = c.load_params()
    extract_all(read_labels(c.LABELS_CSV), c.RAW_DIR / p["dataset"]["name"], c.POSES_DIR, Path(p["pose_model"]))


def _features() -> None:
    from tennis_ml.features import build

    p = c.load_params()
    build(read_labels(c.LABELS_CSV), c.POSES_DIR, p["classes"], p["pose_seq"]["seq_len"], c.FEATURES_NPZ)


def _train() -> None:
    from tennis_ml.train import train

    train(c.FEATURES_NPZ, c.load_params(), c.POSE_SEQ_DIR)


def _export() -> None:
    from tennis_ml.export import export

    export(c.FEATURES_NPZ, c.POSE_SEQ_DIR, c.load_params(), c.POSE_SEQ_EXPORT, c.METRICS_DIR / "pose_seq.json",
           c.LABELS_CSV)


def _yolo_dataset() -> None:
    from tennis_ml.yolo import build_dataset

    p = c.load_params()
    build_dataset(read_labels(c.LABELS_CSV), c.RAW_DIR / p["dataset"]["name"], c.POSES_DIR, p["classes"],
                  p["yolo"]["frames_per_clip"], c.YOLO_DATASET)


def _yolo_train() -> None:
    from tennis_ml.yolo import evaluate_clips, train_and_export, write_meta

    p = c.load_params()
    samples = read_labels(c.LABELS_CSV)
    export_dir = train_and_export(c.YOLO_DATASET, p, c.YOLO_DIR, c.YOLO_EXPORT)
    write_meta(export_dir, p, p["classes"], c.LABELS_CSV, p["version"] or "yolo-dev")
    metrics = evaluate_clips(export_dir, samples, c.RAW_DIR / p["dataset"]["name"], c.POSES_DIR)
    c.METRICS_DIR.mkdir(parents=True, exist_ok=True)
    (c.METRICS_DIR / "yolo.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")


STAGES: dict[str, Callable[[], None]] = {
    "prepare": _prepare, "extract": _extract, "features": _features, "train": _train, "export": _export,
    "yolo-dataset": _yolo_dataset, "yolo-train": _yolo_train,
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tennis_ml", description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)
    for name in STAGES:
        sub.add_parser(name)
    toy = sub.add_parser("make-toy", help="genera poses sintéticas en data/raw/toy")
    toy.add_argument("--players", type=int, default=10)
    pub = sub.add_parser("publish", help="sube un modelo exportado al bucket del worker")
    pub.add_argument("export_dir", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.stage == "make-toy":
        from tennis_ml.toy import make_toy

        make_toy(c.RAW_DIR / "toy", players=args.players)
    elif args.stage == "publish":
        from tennis_ml.publish import main as publish_main

        publish_main(args.export_dir)
    else:
        STAGES[args.stage]()
