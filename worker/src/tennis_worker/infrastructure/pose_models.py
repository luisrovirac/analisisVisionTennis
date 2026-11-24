"""Modelos ONNX oficiales de RTMPose (OpenMMLab, Apache-2.0): descarga fijada y verificada por SHA-256.

Solo se descargan si POSE_ESTIMATOR=rtmpose_motionbert, la primera vez, a la caché de modelos del worker.
Una URL o un archivo alterado detienen el arranque (no se usa un modelo que no sea exactamente este).
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE = "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"


@dataclass(frozen=True)
class PinnedModel:
    name: str
    url: str
    sha256: str


YOLOX_M_HUMANART = PinnedModel(
    "yolox_m_humanart", _BASE + "yolox_m_8xb8-300e_humanart-c2c7a14a.zip",
    "a000224fd8ba283202bc62d4a5fcdfe353adb9f468777dbac1ea2ada2093adde")
RTMPOSE_M_BODY7 = PinnedModel(
    "rtmpose_m_body7_256x192", _BASE + "rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip",
    "f7fbb6c5c11a1bb70f3d445e4ddec5d144ea89ad5649c081ef976f9b24a0b741")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_onnx(model: PinnedModel, cache_dir: Path) -> Path:
    """Ruta local de end2end.onnx; descarga y verifica el zip si aún no está en caché."""
    target = cache_dir / "rtmpose" / model.name / "end2end.onnx"
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = target.parent / "model.zip"
    logger.info("descargando modelo de pose", extra={"model": model.name, "url": model.url})
    with urllib.request.urlopen(model.url, timeout=120) as response, archive.open("wb") as out:
        shutil.copyfileobj(response, out)
    actual = sha256_of(archive)
    if actual != model.sha256:
        archive.unlink()
        raise RuntimeError(f"{model.name}: SHA-256 {actual} no coincide con el fijado {model.sha256}")
    with zipfile.ZipFile(archive) as zf:
        member = next(n for n in zf.namelist() if n.endswith("end2end.onnx"))
        with zf.open(member) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    archive.unlink()
    return target
