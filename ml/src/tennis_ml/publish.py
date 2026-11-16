"""Publica un modelo exportado donde lo lee el worker y lo marca como `production` en MLflow.

    python -m tennis_ml publish export/pose_seq

Sube model.onnx y meta.json a s3://<S3_BUCKET>/models/<clasificador>/<versión>/. Para desplegarlo,
pon POSE_SEQ_MODEL_VERSION=<versión> (o YOLO_MODEL_VERSION) en el entorno del worker y reinícialo.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import boto3
import mlflow
from mlflow.tracking import MlflowClient
from tennis_worker.pipeline.classification.model import META_FILE, MODEL_FILE, ModelMeta

logger = logging.getLogger(__name__)

REGISTERED = {"pose_seq": "stroke-pose-seq", "yolo": "stroke-yolo"}


def publish(export_dir: Path, bucket: str, prefix: str = "models", alias: str = "production") -> str:
    meta = ModelMeta.model_validate_json((export_dir / META_FILE).read_text(encoding="utf-8"))
    s3 = boto3.client("s3", endpoint_url=os.environ.get("AWS_ENDPOINT_URL"))
    for name in (MODEL_FILE, META_FILE):
        s3.upload_file(str(export_dir / name), bucket, f"{prefix}/{meta.classifier}/{meta.version}/{name}")
    if meta.mlflow_run_id:
        _set_alias(REGISTERED[meta.classifier], meta.mlflow_run_id, alias)
    return meta.version


def _set_alias(model_name: str, run_id: str, alias: str) -> None:
    client = MlflowClient(mlflow.get_tracking_uri())
    versions = client.search_model_versions(f"name='{model_name}' and run_id='{run_id}'")
    if versions:
        client.set_registered_model_alias(model_name, alias, versions[0].version)


def main(export_dir: Path) -> None:
    bucket = os.environ["S3_BUCKET"]
    version = publish(export_dir, bucket)
    classifier = json.loads((export_dir / META_FILE).read_text(encoding="utf-8"))["classifier"]
    logger.info("publicado %s@%s en s3://%s/models/%s/%s/", classifier, version, bucket, classifier, version)
    logger.info("despliega con: %s_MODEL_VERSION=%s", classifier.upper(), version)
