"""Fuentes de la base de conocimiento.

- YamlKnowledgeBaseRepository: <dir>/<golpe>.yaml. Es la semilla y sirve para tests y desarrollo sin BD.
- PublishedKnowledgeBaseRepository: la versión ACTIVA publicada en Postgres (kb_releases). La publica la
  API cuando un validador aprueba la propuesta de un experto; el worker la toma sin reiniciar.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import ValidationError

from tennis_worker.contracts import StrokeType
from tennis_worker.errors import UnsupportedStrokeError
from tennis_worker.pipeline.knowledge.models import KnowledgeBase

logger = logging.getLogger("tennis_worker.knowledge")


class KnowledgeBaseRepository(Protocol):
    def load(self, stroke: StrokeType) -> KnowledgeBase: ...


class YamlKnowledgeBaseRepository:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def load(self, stroke: StrokeType) -> KnowledgeBase:
        path = self._directory / f"{stroke}.yaml"
        if not path.exists():
            raise UnsupportedStrokeError(stroke)
        return _parse(path)

    def load_all(self) -> list[KnowledgeBase]:
        """Valida todos los YAML al arrancar (fail fast ante reglas mal escritas)."""
        return [_parse(path) for path in sorted(self._directory.glob("*.yaml"))]


@cache
def _parse(path: Path) -> KnowledgeBase:
    with path.open(encoding="utf-8") as fh:
        return KnowledgeBase.model_validate(yaml.safe_load(fh))


@dataclass(frozen=True)
class StoredRelease:
    id: str
    snapshot: Mapping[str, Any]


class ReleaseSource(Protocol):
    """Acceso a las versiones publicadas (implementación SQL en infrastructure/knowledge_store.py)."""

    def active(self, stroke: StrokeType) -> StoredRelease | None: ...

    def latest_valid(self, stroke: StrokeType, exclude_id: str) -> StoredRelease | None: ...

    def mark_invalid(self, release_id: str, reason: str) -> None: ...


@dataclass
class _Cached:
    release_id: str
    kb: KnowledgeBase
    checked_at: float


class PublishedKnowledgeBaseRepository:
    """Carga la versión activa de cada golpe y la cachea. Cada `refresh_seconds` comprueba si hay una
    versión activa nueva (una consulta barata por id). Nunca usa una versión que no valida: la marca
    como `invalid` y sigue con la que tenía (o con la última válida si aún no tenía ninguna)."""

    def __init__(self, source: ReleaseSource, metric_ids: Callable[[StrokeType], set[str]],
                 refresh_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._source = source
        self._metric_ids = metric_ids
        self._refresh = refresh_seconds
        self._clock = clock
        self._cache: dict[str, _Cached] = {}

    def load(self, stroke: StrokeType) -> KnowledgeBase:
        cached = self._cache.get(stroke)
        now = self._clock()
        if cached and now - cached.checked_at < self._refresh:
            return cached.kb
        try:
            release = self._source.active(stroke)
        except Exception:
            if cached is None:
                raise  # sin caché no hay con qué seguir: error de infraestructura, se reintenta
            logger.warning("no se pudo comprobar la versión de la base de conocimiento; se usa la cacheada",
                           exc_info=True, extra={"stroke": stroke})
            return cached.kb
        if release is None:
            raise UnsupportedStrokeError(stroke)
        if cached and cached.release_id == release.id:
            cached.checked_at = now
            return cached.kb
        kb = self._accept(stroke, release, cached)
        return kb

    def _accept(self, stroke: StrokeType, release: StoredRelease, cached: _Cached | None) -> KnowledgeBase:
        kb, problem = self._validate(stroke, release)
        if kb is not None:
            self._cache[stroke] = _Cached(release.id, kb, self._clock())
            logger.info("base de conocimiento cargada", extra={"kb": kb.label, "release_id": release.id})
            return kb
        logger.error("versión de la base de conocimiento inválida; se descarta",
                     extra={"release_id": release.id, "reason": problem})
        self._source.mark_invalid(release.id, problem)
        if cached is not None:
            cached.checked_at = self._clock()
            return cached.kb
        fallback = self._source.latest_valid(stroke, exclude_id=release.id)
        if fallback is None:
            raise UnsupportedStrokeError(stroke)
        return self._accept(stroke, fallback, None)

    def _validate(self, stroke: StrokeType, release: StoredRelease) -> tuple[KnowledgeBase | None, str]:
        try:
            kb = KnowledgeBase.model_validate(release.snapshot)
        except ValidationError as exc:
            return None, str(exc)[:1000]
        if kb.stroke != stroke:
            return None, f"la versión es de {kb.stroke}, no de {stroke}"
        unknown = {m for rule in kb.rules for m in rule.metric_ids} - self._metric_ids(stroke)
        if unknown:
            return None, f"métricas desconocidas {sorted(unknown)}"
        return kb, ""
