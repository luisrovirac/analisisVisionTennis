"""Actualiza el estado y el resultado de los análisis. El esquema lo gestiona la API (api/migrations)."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from tennis_worker.contracts import Report
from tennis_worker.infrastructure.retry import with_backoff

_START_SQL = """
UPDATE analyses SET status = 'processing', attempts = attempts + 1, updated_at = now()
WHERE id = %s AND status IN ('queued', 'processing')
RETURNING attempts"""

# stroke_type se rellena con el golpe detectado cuando el usuario no lo declaró.
_COMPLETE_SQL = """
UPDATE analyses SET status = 'completed', report = %s, kb_version = %s,
       stroke_type = COALESCE(stroke_type, %s),
       error_code = NULL, error_message = NULL, updated_at = now()
WHERE id = %s"""

_FAIL_SQL = """
UPDATE analyses SET status = 'failed', error_code = %s, error_message = %s, updated_at = now()
WHERE id = %s"""


class PostgresAnalysisRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def start_processing(self, analysis_id: str) -> int | None:
        """Marca el análisis en proceso y devuelve el nº de intento; None si ya no está pendiente."""
        row = self._execute(_START_SQL, (analysis_id,), fetch=True)
        return int(row[0]) if row else None

    def complete(self, analysis_id: str, report: Report) -> None:
        payload = Jsonb(report.model_dump(mode="json"))
        self._execute(_COMPLETE_SQL, (payload, report.knowledge_base_version, report.stroke_type, analysis_id))

    def fail(self, analysis_id: str, code: str, message: str) -> None:
        self._execute(_FAIL_SQL, (code, message, analysis_id))

    def _execute(self, sql: str, params: tuple[Any, ...], fetch: bool = False) -> tuple[Any, ...] | None:
        def run() -> tuple[Any, ...] | None:
            with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone() if fetch else None

        return with_backoff(run, retry_on=(psycopg.OperationalError,))
