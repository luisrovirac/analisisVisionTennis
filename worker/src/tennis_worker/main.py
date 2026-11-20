"""Raíz de composición del worker: único lugar donde se instancian las implementaciones concretas."""

from __future__ import annotations

import logging
import signal
import socket
from collections.abc import Callable
from types import FrameType

import boto3
import psycopg
import redis
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from tennis_worker.application import JobProcessor, WorkerLoop
from tennis_worker.config import Settings
from tennis_worker.infrastructure.analysis_repository import PostgresAnalysisRepository
from tennis_worker.infrastructure.knowledge_store import PostgresKnowledgeStore
from tennis_worker.infrastructure.model_store import ModelStore
from tennis_worker.infrastructure.redis_consumer import RedisJobConsumer
from tennis_worker.infrastructure.s3_store import S3ObjectStore
from tennis_worker.logging_setup import configure_logging
from tennis_worker.observability import Instrumentation, setup_telemetry
from tennis_worker.pipeline.biomechanics.registry import MetricRegistry, default_registry
from tennis_worker.pipeline.classification import (
    COMBINERS,
    PoseSequenceClassifier,
    StrokeClassifier,
    StrokeRecognizer,
    UserProvidedClassifier,
    YoloFrameClassifier,
)
from tennis_worker.pipeline.classification.model import OnnxModel
from tennis_worker.pipeline.knowledge.engine import RuleEngine
from tennis_worker.pipeline.knowledge.repository import (
    KnowledgeBaseRepository,
    PublishedKnowledgeBaseRepository,
    YamlKnowledgeBaseRepository,
)
from tennis_worker.pipeline.orchestrator import AnalysisPipeline, PipelineStages
from tennis_worker.pipeline.pose.mediapipe_estimator import MediaPipePoseEstimator
from tennis_worker.pipeline.prioritization import CategoryImpactPrioritizer
from tennis_worker.pipeline.quality import QualityAssessor
from tennis_worker.pipeline.segmentation import WristSpeedSegmenter
from tennis_worker.pipeline.video import OpenCVVideoReader, VideoReader

TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    psycopg.OperationalError, redis.RedisError, BotoCoreError, ClientError,
)

logger = logging.getLogger("tennis_worker")


def build_recognizer(settings: Settings, models: ModelStore, video_reader: VideoReader) -> StrokeRecognizer:
    """Registro de clasificadores: añadir uno es añadir una entrada, sin tocar el pipeline (OCP).
    Los modelos se cargan aquí, al arrancar: un modelo ausente o incompatible detiene el worker."""
    builders: dict[str, Callable[[], StrokeClassifier]] = {
        "user": UserProvidedClassifier,
        "pose_seq": lambda: PoseSequenceClassifier(
            OnnxModel(models.ensure("pose_seq", settings.pose_seq_model_version), "pose_seq")),
        "yolo": lambda: YoloFrameClassifier(
            OnnxModel(models.ensure("yolo", settings.yolo_model_version), "yolo"), video_reader),
    }
    classifiers = [builders[name]() for name in settings.classifier_ids]
    combiner = COMBINERS[settings.stroke_combiner](settings.stroke_min_confidence, settings.vote_weights)
    logger.info("clasificación del golpe", extra={"classifiers": settings.classifier_ids,
                                                  "combiner": settings.stroke_combiner})
    return StrokeRecognizer(classifiers, combiner)


def build_pipeline(settings: Settings, registry: MetricRegistry, knowledge: KnowledgeBaseRepository,
                   recognizer: StrokeRecognizer, obs: Instrumentation) -> AnalysisPipeline:
    return AnalysisPipeline(PipelineStages(
        video_reader=OpenCVVideoReader(max_seconds=settings.max_video_seconds),
        pose_estimator=MediaPipePoseEstimator(settings.pose_model_path),
        quality=QualityAssessor(min_detection_rate=settings.min_pose_detection_rate),
        classifier=recognizer,
        segmenter=WristSpeedSegmenter(),
        metrics=registry,
        knowledge=knowledge,
        rules=RuleEngine(min_confidence=settings.min_metric_confidence),
        prioritizer=CategoryImpactPrioritizer(),
    ), obs)


def validate_knowledge(knowledge: YamlKnowledgeBaseRepository, registry: MetricRegistry) -> None:
    """Fail fast: reglas mal escritas o que citan métricas inexistentes detienen el arranque."""
    for kb in knowledge.load_all():
        unknown = {m for rule in kb.rules for m in rule.metric_ids} - registry.metric_ids(kb.stroke)
        if unknown:
            raise SystemExit(f"Base de conocimiento {kb.label} inválida: métricas desconocidas {sorted(unknown)}")
        if not any(rule.validated for rule in kb.rules):
            logger.warning("base de conocimiento semilla SIN validar", extra={"kb": kb.label})


def build_knowledge(settings: Settings, registry: MetricRegistry) -> KnowledgeBaseRepository:
    """La semilla YAML se valida siempre. Con Postgres, además: se publica el catálogo de métricas,
    se siembra la primera versión de cada golpe y se lee la versión activa (que gestiona el portal)."""
    seed = YamlKnowledgeBaseRepository(settings.knowledge_base_dir)
    validate_knowledge(seed, registry)
    if settings.knowledge_source == "yaml":
        return seed
    store = PostgresKnowledgeStore(settings.database_url.get_secret_value())
    store.publish_catalog(registry.catalog())
    for label in store.seed(seed.load_all()):
        logger.info("base de conocimiento sembrada en Postgres", extra={"kb": label})
    return PublishedKnowledgeBaseRepository(store, registry.metric_ids, settings.knowledge_refresh_seconds)


def build_loop(settings: Settings, obs: Instrumentation) -> WorkerLoop:
    registry = default_registry()
    knowledge = build_knowledge(settings, registry)
    s3 = boto3.client(
        "s3", endpoint_url=settings.s3_endpoint_url, region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key, aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        config=BotoConfig(signature_version="s3v4", retries={"mode": "standard", "max_attempts": 5}),
    )
    store = S3ObjectStore(s3, settings.s3_bucket)
    recognizer = build_recognizer(settings, ModelStore(store, settings.stroke_models_dir),
                                  OpenCVVideoReader(max_seconds=settings.max_video_seconds))
    processor = JobProcessor(
        repo=PostgresAnalysisRepository(settings.database_url.get_secret_value()),
        store=store,
        pipeline=build_pipeline(settings, registry, knowledge, recognizer, obs),
        max_attempts=settings.max_attempts,
        instrumentation=obs,
    )
    consumer = RedisJobConsumer(
        redis.Redis.from_url(settings.redis_url), settings.job_stream, settings.job_group,
        consumer=socket.gethostname(), claim_idle_ms=settings.job_claim_idle_seconds * 1000,
        dlq_stream=settings.dlq_stream,
    )
    return WorkerLoop(consumer, processor, TRANSIENT_ERRORS, obs)


def main() -> None:
    settings = Settings()  # los valores vienen del entorno
    configure_logging(settings.log_level)
    telemetry = setup_telemetry(settings.otel_service_name, settings.otel_exporter_otlp_endpoint)
    loop = build_loop(settings, telemetry.instrumentation)

    def _stop(signum: int, _frame: FrameType | None) -> None:
        logger.info("señal recibida, terminando tras el trabajo actual", extra={"signal": signum})
        loop.stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        loop.run()
    finally:
        telemetry.shutdown()  # vacía los spans y métricas pendientes antes de salir


if __name__ == "__main__":
    main()
