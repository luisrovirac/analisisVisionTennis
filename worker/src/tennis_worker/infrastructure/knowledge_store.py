"""Base de conocimiento en Postgres (tablas creadas por la API: api/migrations/0006_knowledge).

El worker solo: publica su catálogo de métricas (para que el portal valide las reglas contra lo que el
worker sabe medir), siembra la primera versión desde el YAML si un golpe aún no tiene ninguna, lee la
versión activa y marca como `invalid` una versión que no valida."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from tennis_worker.contracts import StrokeType
from tennis_worker.infrastructure.retry import with_backoff
from tennis_worker.pipeline.biomechanics.registry import CatalogEntry
from tennis_worker.pipeline.knowledge.models import KnowledgeBase
from tennis_worker.pipeline.knowledge.repository import StoredRelease

_UPSERT_METRIC_SQL = """
INSERT INTO metric_catalog (stroke, metric_id, label, unit, updated_at) VALUES (%s, %s, %s, %s, now())
ON CONFLICT (stroke, metric_id) DO UPDATE SET label = EXCLUDED.label, unit = EXCLUDED.unit, updated_at = now()"""

_SEED_SQL = """
INSERT INTO kb_releases (stroke, version, snapshot, changelog, active, status, activated_at)
SELECT %s, %s, %s, %s, true, 'valid', now()
WHERE NOT EXISTS (SELECT 1 FROM kb_releases WHERE stroke = %s)"""

_ACTIVE_SQL = "SELECT id::text, snapshot FROM kb_releases WHERE stroke = %s AND active"

_LATEST_VALID_SQL = """
SELECT id::text, snapshot FROM kb_releases
WHERE stroke = %s AND status = 'valid' AND id <> %s::uuid
ORDER BY created_at DESC LIMIT 1"""

_MARK_INVALID_SQL = "UPDATE kb_releases SET status = 'invalid', invalid_reason = %s WHERE id = %s::uuid"


class PostgresKnowledgeStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def publish_catalog(self, entries: Iterable[CatalogEntry]) -> None:
        rows = [(e.stroke, e.metric_id, e.label, e.unit) for e in entries]

        def run() -> None:
            with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
                cur.executemany(_UPSERT_METRIC_SQL, rows)

        with_backoff(run, retry_on=(psycopg.OperationalError,))

    def seed(self, bases: Iterable[KnowledgeBase]) -> list[str]:
        """Importa cada YAML como versión activa SOLO si el golpe no tiene ninguna versión todavía.
        Devuelve las etiquetas sembradas. Varios workers a la vez: el lock evita sembrar dos veces."""
        seeded: list[str] = []

        def run() -> None:
            with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext('kb_seed'))")
                for kb in bases:
                    snapshot = kb.model_dump(mode="json", by_alias=True, exclude_none=True)
                    cur.execute(_SEED_SQL, (kb.stroke, kb.version, Jsonb(snapshot),
                                            f"Versión inicial importada de {kb.stroke}.yaml", kb.stroke))
                    if cur.rowcount:
                        seeded.append(kb.label)

        with_backoff(run, retry_on=(psycopg.OperationalError,))
        return seeded

    def active(self, stroke: StrokeType) -> StoredRelease | None:
        return self._fetch(_ACTIVE_SQL, (stroke,))

    def latest_valid(self, stroke: StrokeType, exclude_id: str) -> StoredRelease | None:
        return self._fetch(_LATEST_VALID_SQL, (stroke, exclude_id))

    def mark_invalid(self, release_id: str, reason: str) -> None:
        def run() -> None:
            with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
                cur.execute(_MARK_INVALID_SQL, (reason, release_id))

        with_backoff(run, retry_on=(psycopg.OperationalError,))

    def _fetch(self, sql: str, params: tuple[Any, ...]) -> StoredRelease | None:
        def run() -> StoredRelease | None:
            with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
            if row is None:
                return None
            snapshot = row[1] if isinstance(row[1], dict) else json.loads(row[1])
            return StoredRelease(id=row[0], snapshot=snapshot)

        return with_backoff(run, retry_on=(psycopg.OperationalError,))
