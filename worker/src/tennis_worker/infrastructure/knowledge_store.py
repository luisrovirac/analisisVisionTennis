"""Base de conocimiento en Postgres (tablas creadas por la API: api/migrations/0006_knowledge).

El worker solo: publica su catálogo de métricas (para que el portal valide las reglas contra lo que el
worker sabe medir), siembra la primera versión desde el YAML si un golpe aún no tiene ninguna (o la
actualiza si nadie la ha tocado desde el portal, ver `seed_action`), lee la versión activa y marca como
`invalid` una versión que no valida."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

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

_SEED_STATE_SQL = """
SELECT
    EXISTS (SELECT 1 FROM kb_releases WHERE stroke = %(stroke)s),
    (SELECT version FROM kb_releases WHERE stroke = %(stroke)s AND active),
    EXISTS (SELECT 1 FROM kb_releases WHERE stroke = %(stroke)s AND proposal_id IS NOT NULL)
        OR EXISTS (SELECT 1 FROM kb_proposals WHERE stroke = %(stroke)s),
    EXISTS (SELECT 1 FROM kb_releases WHERE stroke = %(stroke)s AND version = %(version)s)"""

_DEACTIVATE_SQL = "UPDATE kb_releases SET active = false WHERE stroke = %s AND active"

_INSERT_SEED_SQL = """
INSERT INTO kb_releases (stroke, version, snapshot, changelog, active, status, activated_at)
VALUES (%s, %s, %s, %s, true, 'valid', now())"""

SeedAction = Literal["insert", "upgrade", "keep"]


@dataclass(frozen=True)
class SeedState:
    has_releases: bool
    active_version: str | None
    touched_in_portal: bool  # hay propuestas o versiones publicadas desde el portal para este golpe
    seed_version_exists: bool


def _semver(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def seed_action(state: SeedState, seed_version: str) -> SeedAction:
    """Qué hacer con la semilla YAML de un golpe al arrancar.

    - insert: el golpe no tiene ninguna versión → se importa la semilla.
    - upgrade: la versión activa es una semilla que nadie ha tocado desde el portal (sin propuestas ni
      versiones publicadas por expertos) y el YAML trae una versión mayor → se publica la nueva semilla.
    - keep: en cualquier otro caso. Una vez que los expertos gobiernan un golpe desde el portal, el YAML
      nunca pisa su trabajo; y si alguien reactivó una versión anterior (rollback), se respeta.
    """
    if not state.has_releases:
        return "insert"
    if state.touched_in_portal or state.active_version is None or state.seed_version_exists:
        return "keep"
    new, current = _semver(seed_version), _semver(state.active_version)
    if new is None or current is None or new <= current:
        return "keep"
    return "upgrade"

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
        """Importa (o actualiza, ver `seed_action`) cada YAML como versión activa. Devuelve las etiquetas
        publicadas. Varios workers a la vez: el lock evita sembrar dos veces."""
        seeded: list[str] = []
        bases = list(bases)

        def run() -> None:
            seeded.clear()  # with_backoff puede reintentar: la transacción anterior se deshizo
            with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext('kb_seed'))")
                for kb in bases:
                    cur.execute(_SEED_STATE_SQL, {"stroke": kb.stroke, "version": kb.version})
                    row = cur.fetchone()  # un SELECT sin FROM siempre devuelve una fila
                    if row is None:
                        raise RuntimeError("kb_seed: la consulta de estado no devolvió filas")
                    state = SeedState(bool(row[0]), row[1], bool(row[2]), bool(row[3]))
                    action = seed_action(state, kb.version)
                    if action == "keep":
                        continue
                    if action == "upgrade":
                        cur.execute(_DEACTIVATE_SQL, (kb.stroke,))
                        changelog = (f"Semilla actualizada desde {kb.stroke}.yaml "
                                     f"({state.active_version} → {kb.version}).")
                    else:
                        changelog = f"Versión inicial importada de {kb.stroke}.yaml"
                    snapshot = kb.model_dump(mode="json", by_alias=True, exclude_none=True)
                    cur.execute(_INSERT_SEED_SQL, (kb.stroke, kb.version, Jsonb(snapshot), changelog))
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
