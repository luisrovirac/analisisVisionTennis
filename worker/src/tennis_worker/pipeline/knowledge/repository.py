"""Carga las bases de conocimiento desde <dir>/<golpe>.yaml."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Protocol

import yaml

from tennis_worker.contracts import StrokeType
from tennis_worker.errors import UnsupportedStrokeError
from tennis_worker.pipeline.knowledge.models import KnowledgeBase


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
