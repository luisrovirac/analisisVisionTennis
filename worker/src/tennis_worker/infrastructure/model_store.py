"""Descarga los modelos exportados por ml/ (model.onnx + meta.json) desde el bucket al arrancar.

El worker no habla con MLflow: lee una versión fija de `models/<clasificador>/<versión>/`. Así el
arranque es determinista y no depende de que el servidor de experimentos esté disponible.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from tennis_worker.pipeline.classification.model import META_FILE, MODEL_FILE

logger = logging.getLogger(__name__)


class Downloader(Protocol):
    def download(self, key: str, destination: Path) -> None: ...


class ModelStore:
    def __init__(self, downloader: Downloader, cache_dir: Path, prefix: str = "models") -> None:
        self._downloader = downloader
        self._cache_dir = cache_dir
        self._prefix = prefix

    def ensure(self, classifier: str, version: str) -> Path:
        """Ruta local del modelo. Sin versión se usa `<cache>/<clasificador>/` (montado a mano)."""
        if not version:
            return self._cache_dir / classifier
        target = self._cache_dir / classifier / version
        for name in (META_FILE, MODEL_FILE):
            if not (target / name).exists():
                target.mkdir(parents=True, exist_ok=True)
                key = f"{self._prefix}/{classifier}/{version}/{name}"
                logger.info("descargando modelo", extra={"key": key})
                self._downloader.download(key, target / name)
        return target
