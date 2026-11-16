"""Etapa extract_poses: video → pose JSON con EXACTAMENTE el mismo estimador y postproceso que el worker."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from tennis_worker.pipeline.pose.mediapipe_estimator import MediaPipePoseEstimator
from tennis_worker.pipeline.pose.postprocess import interpolate_gaps
from tennis_worker.pipeline.pose.serialization import to_artifact
from tennis_worker.pipeline.video import OpenCVVideoReader

from tennis_ml.datasets import POSE_SUFFIX, Sample

logger = logging.getLogger(__name__)

MAX_CLIP_SECONDS = 60.0  # THETIS dura ~2-5 s; el límite solo protege de archivos anómalos


def extract_all(samples: list[Sample], raw_dir: Path, out_dir: Path, model_path: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    estimator: MediaPipePoseEstimator | None = None  # se crea solo si hay videos (toy no los tiene)
    reader = OpenCVVideoReader(max_seconds=MAX_CLIP_SECONDS)
    for index, sample in enumerate(samples, start=1):
        source, target = raw_dir / sample.source, out_dir / f"{sample.id}.json"
        if sample.source.endswith(POSE_SUFFIX):
            shutil.copyfile(source, target)
            continue
        estimator = estimator or MediaPipePoseEstimator(model_path)
        with reader.open(source) as stream:
            pose = interpolate_gaps(estimator.estimate(stream))
        target.write_text(json.dumps(to_artifact(pose)), encoding="utf-8")
        if index % 50 == 0:
            logger.info("poses extraídas", extra={"done": index, "total": len(samples)})
