"""Consumidor de Redis Streams con grupo de consumidores, recuperación de mensajes huérfanos y DLQ."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import redis


@dataclass(frozen=True)
class QueuedJob:
    message_id: str
    payload: str
    traceparent: str | None = None  # contexto W3C de la petición que encoló el trabajo


class RedisJobConsumer:
    def __init__(self, client: redis.Redis, stream: str, group: str, consumer: str,
                 claim_idle_ms: int, dlq_stream: str, block_ms: int = 5000) -> None:
        self._client = client
        self._stream = stream
        self._group = group
        self._consumer = consumer
        self._claim_idle_ms = claim_idle_ms
        self._dlq_stream = dlq_stream
        self._block_ms = block_ms

    def ensure_group(self) -> None:
        try:
            self._client.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def next_job(self) -> QueuedJob | None:
        """Primero recupera mensajes de workers caídos (pendientes demasiado tiempo); luego lee nuevos."""
        return self._claim_stale() or self._read_new()

    def ack(self, message_id: str) -> None:
        self._client.xack(self._stream, self._group, message_id)

    def dead_letter(self, job: QueuedJob, reason: str) -> None:
        """Copia el mensaje a la DLQ y lo confirma en el stream principal, en una transacción
        (MULTI/EXEC): nunca queda en los dos sitios ni se pierde entre ellos."""
        entry = {"payload": job.payload, "reason": reason, "original_id": job.message_id,
                 "failed_at": datetime.now(timezone.utc).isoformat()}
        if job.traceparent:
            entry["traceparent"] = job.traceparent
        pipe = self._client.pipeline(transaction=True)
        pipe.xadd(self._dlq_stream, entry)  # type: ignore[arg-type]
        pipe.xack(self._stream, self._group, job.message_id)
        pipe.execute()

    def _claim_stale(self) -> QueuedJob | None:
        result: Any = self._client.xautoclaim(self._stream, self._group, self._consumer,
                                              min_idle_time=self._claim_idle_ms, count=1)
        return _first(result[1])

    def _read_new(self) -> QueuedJob | None:
        result: Any = self._client.xreadgroup(self._group, self._consumer, {self._stream: ">"},
                                              count=1, block=self._block_ms)
        return _first(result[0][1]) if result else None


def _first(messages: list[Any]) -> QueuedJob | None:
    for message_id, fields in messages:
        if fields:  # XAUTOCLAIM puede devolver entradas ya borradas sin campos
            traceparent = fields.get(b"traceparent")
            return QueuedJob(message_id=_text(message_id), payload=_text(fields[b"payload"]),
                             traceparent=_text(traceparent) if traceparent else None)
    return None


def _text(value: bytes | str) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value
