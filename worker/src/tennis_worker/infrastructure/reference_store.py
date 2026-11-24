"""Golpes de referencia en el bucket (references/index.json + references/<golpe>/<ref_id>.json).

El índice se relee cada `refresh_seconds` (registrar una referencia no exige reiniciar el worker); cada
golpe de referencia se descarga una sola vez por registro (`registered_at`) y queda en memoria."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from tennis_worker.contracts import CameraView, StrokeType
from tennis_worker.pipeline.reference.comparator import ReferenceStroke
from tennis_worker.pipeline.reference.serialization import INDEX_KEY, from_reference_artifact

logger = logging.getLogger(__name__)


class JsonReader(Protocol):
    def get_json(self, key: str) -> dict[str, Any] | None: ...


class S3ReferenceStore:
    def __init__(self, store: JsonReader, refresh_seconds: float = 300.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._store = store
        self._refresh = refresh_seconds
        self._clock = clock
        self._index: list[dict[str, Any]] = []
        self._loaded_at: float | None = None
        self._strokes: dict[str, ReferenceStroke] = {}

    def references(self, stroke: StrokeType, view: CameraView) -> list[ReferenceStroke]:
        self._refresh_index()
        found = []
        for entry in self._index:
            if entry.get("stroke") != stroke or entry.get("camera_view") != view:
                continue
            ref = self._load(entry)
            if ref is not None:
                found.append(ref)
        return found

    def _refresh_index(self) -> None:
        now = self._clock()
        if self._loaded_at is not None and now - self._loaded_at < self._refresh:
            return
        data = self._store.get_json(INDEX_KEY)
        self._index = list((data or {}).get("references", []))
        self._loaded_at = now

    def _load(self, entry: dict[str, Any]) -> ReferenceStroke | None:
        key = str(entry.get("key", ""))
        cache_key = f"{key}@{entry.get('registered_at', '')}"  # re-registrar con el mismo id invalida la caché
        if cache_key not in self._strokes:
            data = self._store.get_json(key)
            if data is None:
                logger.warning("referencia del índice sin archivo", extra={"key": key})
                return None
            self._strokes[cache_key] = from_reference_artifact(data)
        return self._strokes[cache_key]
