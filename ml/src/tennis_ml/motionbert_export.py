"""Exporta MotionBERT (lifting 2D → 3D) a ONNX para el estimador rtmpose_motionbert del worker.

    python -m tennis_ml motionbert --out export/motionbert [--publish]

Descarga de Hugging Face (walterzhu/MotionBERT, Apache-2.0), fijado a una revisión concreta, el código del
modelo (lib/model/DSTformer.py, drop.py) y el checkpoint MB_ft_h36m_global_lite (el que usa la inferencia
oficial en video, infer_wild.py). Comprueba que ONNX Runtime da lo mismo que PyTorch y escribe:

    <out>/model.onnx   entrada "input" (1, 243, 17, 3) → salida "output" (1, 243, 17, 3)
    <out>/meta.json    procedencia (repo, revisión, checkpoint, SHA-256) y formato

Con --publish lo sube a s3://<S3_BUCKET>/models/motionbert/<versión>/; despliega con
POSE_ESTIMATOR=rtmpose_motionbert y MOTIONBERT_MODEL_VERSION=<versión> en el entorno del worker.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import urllib.request
from functools import partial
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

REPO = "walterzhu/MotionBERT"
REVISION = "370a9196aa3c89198b134c82476143b01c0fb32c"
CHECKPOINT = "checkpoint/pose3d/FT_MB_lite_MB_ft_h36m_global_lite/best_epoch.bin"
SOURCES = ("lib/__init__.py", "lib/model/__init__.py", "lib/model/DSTformer.py", "lib/model/drop.py", "LICENSE")
# Configuración del checkpoint (configs/pose3d/MB_ft_h36m_global_lite.yaml).
CONFIG = {"dim_feat": 256, "dim_rep": 512, "depth": 5, "num_heads": 8, "mlp_ratio": 4, "maxlen": 243,
          "num_joints": 17}
CLIP_LEN = 243


def _download(path: str, destination: Path) -> Path:
    target = destination / path
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if path.endswith("__init__.py"):  # el repo no los trae: paquete Python vacío
        target.write_text("", encoding="utf-8")
        return target
    url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{path}"
    logger.info("descargando %s", url)
    with urllib.request.urlopen(url, timeout=300) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build_model(cache: Path):  # type: ignore[no-untyped-def]
    import torch
    from torch import nn

    for source in SOURCES:
        _download(source, cache)
    checkpoint_path = _download(CHECKPOINT, cache)
    sys.path.insert(0, str(cache))
    from lib.model.DSTformer import DSTformer  # código oficial, descargado arriba

    model = DSTformer(dim_in=3, dim_out=3, norm_layer=partial(nn.LayerNorm, eps=1e-6), **CONFIG)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    state = {k.removeprefix("module."): v for k, v in checkpoint["model_pos"].items()}
    model.load_state_dict(state, strict=True)
    model.eval()
    return model, checkpoint_path


def export(out_dir: Path, cache: Path) -> dict[str, object]:
    import onnxruntime as ort
    import torch

    model, checkpoint_path = build_model(cache)
    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / "model.onnx"
    example = torch.zeros(1, CLIP_LEN, CONFIG["num_joints"], 3)
    torch.onnx.export(model, (example,), str(onnx_path), input_names=["input"], output_names=["output"],
                      opset_version=17, dynamo=False)
    rng = np.random.default_rng(0)
    sample = rng.uniform(-1, 1, (1, CLIP_LEN, CONFIG["num_joints"], 3)).astype(np.float32)
    sample[..., 2] = rng.uniform(0.3, 1.0, sample.shape[:-1])
    with torch.no_grad():
        expected = model(torch.from_numpy(sample)).numpy()
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    (actual,) = session.run(None, {"input": sample})
    max_error = float(np.abs(actual - expected).max())
    if max_error > 1e-3:
        raise RuntimeError(f"ONNX y PyTorch difieren (error máximo {max_error:.2e})")
    model_sha = _sha256(onnx_path)
    meta = {
        "classifier": "motionbert",  # nombre del modelo en el bucket (models/<nombre>/<versión>/)
        "version": f"mb-lite-h36m-global-{model_sha[:8]}",
        "source": f"https://huggingface.co/{REPO}", "revision": REVISION, "checkpoint": CHECKPOINT,
        "checkpoint_sha256": _sha256(checkpoint_path), "model_sha256": model_sha, "license": "Apache-2.0",
        "clip_len": CLIP_LEN, "joints": "h36m-17",
        "input": "x,y normalizados con (p - tamaño/2) / (min(w,h)/2) + confianza",
        "flip_test": True, "onnx_vs_torch_max_error": max_error,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    shutil.copy(cache / "LICENSE", out_dir / "LICENSE-MotionBERT")
    logger.info("exportado %s (error ONNX vs PyTorch %.1e)", meta["version"], max_error)
    return meta


def publish(out_dir: Path, bucket: str, prefix: str = "models") -> str:
    import boto3

    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    s3 = boto3.client("s3", endpoint_url=os.environ.get("AWS_ENDPOINT_URL"))
    for name in ("model.onnx", "meta.json"):
        s3.upload_file(str(out_dir / name), bucket, f"{prefix}/motionbert/{meta['version']}/{name}")
    return str(meta["version"])


def main(out_dir: Path, cache: Path, do_publish: bool) -> None:
    meta = export(out_dir, cache)
    if do_publish:
        bucket = os.environ["S3_BUCKET"]
        version = publish(out_dir, bucket)
        logger.info("publicado en s3://%s/models/motionbert/%s/", bucket, version)
    logger.info("despliega con: POSE_ESTIMATOR=rtmpose_motionbert MOTIONBERT_MODEL_VERSION=%s", meta["version"])
