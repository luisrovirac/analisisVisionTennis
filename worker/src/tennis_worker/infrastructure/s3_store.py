"""Almacenamiento de objetos S3 (MinIO/R2). Los reintentos los gestiona botocore (modo standard)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class S3ObjectStore:
    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def download(self, key: str, destination: Path) -> None:
        self._client.download_file(self._bucket, key, str(destination))

    def put_json(self, key: str, data: dict[str, Any]) -> None:
        body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        self._client.put_object(Bucket=self._bucket, Key=key, Body=body, ContentType="application/json")
