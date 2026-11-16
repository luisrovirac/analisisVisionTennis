"""Capa de aplicación: procesa un trabajo (estado, descarga, pipeline, persistencia) y el bucle de consumo."""

from __future__ import annotations

import enum
import logging
import tempfile
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from tennis_worker.contracts import JobMessage, Report
from tennis_worker.errors import AnalysisError
from tennis_worker.infrastructure.redis_consumer import QueuedJob
from tennis_worker.observability import Instrumentation
from tennis_worker.pipeline.orchestrator import PipelineResult
from tennis_worker.pipeline.pose.serialization import to_artifact

logger = logging.getLogger(__name__)

MAX_ATTEMPTS_CODE = "MAX_ATTEMPTS_EXCEEDED"
MAX_ATTEMPTS_MESSAGE = "No pudimos completar el análisis tras varios intentos. Prueba con otro video."
INVALID_PAYLOAD_REASON = "INVALID_PAYLOAD"


class Outcome(enum.Enum):
    COMPLETED = "completed"
    FAILED = "failed"  # problema del video: resultado definitivo para el usuario
    SKIPPED = "skipped"  # ya estaba resuelto (entrega duplicada)
    DEAD_LETTER = "dead_letter"  # agotó los intentos: hay que inspeccionarlo a mano


class AnalysisRepository(Protocol):
    def start_processing(self, analysis_id: str) -> int | None: ...
    def complete(self, analysis_id: str, report: Report) -> None: ...
    def fail(self, analysis_id: str, code: str, message: str) -> None: ...


class ObjectStore(Protocol):
    def download(self, key: str, destination: Path) -> None: ...
    def put_json(self, key: str, data: dict[str, Any]) -> None: ...


class Pipeline(Protocol):
    def run(self, video_path: Path, job: JobMessage) -> PipelineResult: ...


class JobConsumer(Protocol):
    def ensure_group(self) -> None: ...
    def next_job(self) -> QueuedJob | None: ...
    def ack(self, message_id: str) -> None: ...
    def dead_letter(self, job: QueuedJob, reason: str) -> None: ...


class JobProcessor:
    """Los AnalysisError (problemas del video) marcan el análisis como fallido sin reintento.
    Los errores de infraestructura se propagan: el mensaje no se confirma y se reintentará."""

    def __init__(self, repo: AnalysisRepository, store: ObjectStore, pipeline: Pipeline, max_attempts: int,
                 instrumentation: Instrumentation | None = None) -> None:
        self._repo = repo
        self._store = store
        self._pipeline = pipeline
        self._max_attempts = max_attempts
        self._obs = instrumentation or Instrumentation.noop()

    def process(self, job: JobMessage) -> Outcome:
        log = {"analysis_id": job.analysis_id}
        attempt = self._repo.start_processing(job.analysis_id)
        if attempt is None:
            logger.info("análisis ya resuelto, se descarta", extra=log)
            return Outcome.SKIPPED
        if attempt > self._max_attempts:
            self._repo.fail(job.analysis_id, MAX_ATTEMPTS_CODE, MAX_ATTEMPTS_MESSAGE)
            logger.error("máximo de intentos superado; el mensaje va a la DLQ", extra=log)
            self._obs.outcome(Outcome.DEAD_LETTER.value, MAX_ATTEMPTS_CODE)
            return Outcome.DEAD_LETTER
        return self._analyze(job, log | {"attempt": attempt})

    def _analyze(self, job: JobMessage, log: dict[str, Any]) -> Outcome:
        try:
            result = self._run_pipeline(job)
        except AnalysisError as exc:
            self._repo.fail(job.analysis_id, exc.code, exc.user_message)
            logger.info("análisis no realizable", extra=log | {"code": exc.code, "detail": exc.detail})
            self._obs.outcome(Outcome.FAILED.value, exc.code)
            return Outcome.FAILED
        with self._obs.stage("persist"):
            self._store.put_json(f"poses/{job.analysis_id}.json", to_artifact(result.pose))
            self._repo.complete(job.analysis_id, result.report)
        logger.info("análisis completado", extra=log | {"findings": len(result.report.findings)})
        self._obs.outcome(Outcome.COMPLETED.value)
        return Outcome.COMPLETED

    def _run_pipeline(self, job: JobMessage) -> PipelineResult:
        with tempfile.TemporaryDirectory(prefix="tennis-") as workdir:
            video_path = Path(workdir) / "video"
            with self._obs.stage("download"):
                self._store.download(job.video_key, video_path)
            return self._pipeline.run(video_path, job)


class WorkerLoop:
    def __init__(self, consumer: JobConsumer, processor: JobProcessor,
                 transient_errors: tuple[type[Exception], ...],
                 instrumentation: Instrumentation | None = None) -> None:
        self._consumer = consumer
        self._processor = processor
        self._transient_errors = transient_errors
        self._obs = instrumentation or Instrumentation.noop()
        self._running = True

    def run(self) -> None:
        self._consumer.ensure_group()
        logger.info("worker esperando trabajos")
        while self._running:
            self.run_once()

    def stop(self) -> None:
        self._running = False

    def run_once(self) -> None:
        queued = self._consumer.next_job()
        if queued is None:
            return
        try:
            job = JobMessage.model_validate_json(queued.payload)
        except ValidationError as exc:
            logger.error("mensaje inválido; va a la DLQ", extra={"message_id": queued.message_id, "error": str(exc)})
            self._consumer.dead_letter(queued, INVALID_PAYLOAD_REASON)
            return
        with self._obs.job(job.analysis_id, queued.traceparent):
            self._process(queued, job)

    def _process(self, queued: QueuedJob, job: JobMessage) -> None:
        try:
            outcome = self._processor.process(job)
        except self._transient_errors as exc:
            logger.warning("error transitorio; el trabajo se reintentará",
                           extra={"analysis_id": job.analysis_id, "error": str(exc)})
            return
        except Exception:
            # Frontera del bucle: un fallo inesperado (un bug) no debe tumbar el worker. El mensaje no
            # se confirma; se reintenta tras JOB_CLAIM_IDLE_SECONDS y al superar MAX_ATTEMPTS va a la DLQ.
            logger.exception("error inesperado; el trabajo se reintentará", extra={"analysis_id": job.analysis_id})
            return
        if outcome is Outcome.DEAD_LETTER:
            self._consumer.dead_letter(queued, MAX_ATTEMPTS_CODE)
        else:
            self._consumer.ack(queued.message_id)
