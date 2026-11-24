"""Orquestador: compone los pasos del pipeline, todos inyectados (sin lógica de dominio propia)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

from tennis_worker.contracts import (
    Classification,
    JobMessage,
    MetricValue,
    Quality,
    ReferenceComparison,
    Report,
    Segment,
    StrokeType,
)
from tennis_worker.observability import Instrumentation
from tennis_worker.pipeline import insights
from tennis_worker.pipeline.audio import AudioImpactRefiner
from tennis_worker.pipeline.biomechanics.base import MetricContext
from tennis_worker.pipeline.biomechanics.registry import MetricComputer
from tennis_worker.pipeline.classification import ClassificationContext, Decision, StrokeRecognizer
from tennis_worker.pipeline.knowledge.engine import RuleEngine
from tennis_worker.pipeline.knowledge.repository import KnowledgeBaseRepository
from tennis_worker.pipeline.pose import PoseEstimator
from tennis_worker.pipeline.pose.postprocess import interpolate_gaps, smooth_pose
from tennis_worker.pipeline.prioritization import Prioritizer
from tennis_worker.pipeline.quality import QualityAssessor
from tennis_worker.pipeline.reference import ReferenceComparator
from tennis_worker.pipeline.segmentation import StrokeSegmenter
from tennis_worker.pipeline.types import PoseSequence
from tennis_worker.pipeline.video import Frame, VideoReader, VideoStream

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineStages:
    video_reader: VideoReader
    pose_estimator: PoseEstimator
    quality: QualityAssessor
    classifier: StrokeRecognizer
    segmenter: StrokeSegmenter
    metrics: MetricComputer
    knowledge: KnowledgeBaseRepository
    rules: RuleEngine
    prioritizer: Prioritizer
    impact_refiner: AudioImpactRefiner | None = None  # None = impacto solo por la velocidad de la muñeca
    smoothing_seconds: float = 0.0  # ventana del suavizado Savitzky-Golay (0 = desactivado)
    notes: tuple[str, ...] = field(default_factory=tuple)  # razones fijas de fiabilidad (p. ej. estimador)
    comparator: ReferenceComparator | None = None  # None = sin comparación con referentes (DTW)


@dataclass(frozen=True)
class PipelineResult:
    report: Report
    pose: PoseSequence


class AnalysisPipeline:
    """Cada etapa es un span (y una muestra del histograma de duración): la latencia de cada paso
    es visible en la traza que empieza en la petición HTTP de la API."""

    def __init__(self, stages: PipelineStages, instrumentation: Instrumentation | None = None) -> None:
        self._s = stages
        self._obs = instrumentation or Instrumentation.noop()

    def run(self, video_path: Path, job: JobMessage) -> PipelineResult:
        with self._obs.stage("pose"):
            pose = self._estimate_pose(video_path)
        with self._obs.stage("quality"):
            self._s.quality.check(pose)
        with self._obs.stage("classify"):
            decision = self._s.classifier.recognize(ClassificationContext(pose, video_path, job))
            stroke = decision.stroke
            kb = self._s.knowledge.load(stroke)
        with self._obs.stage("segment"):
            segment = self._s.segmenter.segment(pose, job.handedness)
            notes = list(self._s.notes)
            uncertainty = 0.0  # desconocida: la decide la etapa de métricas
            if self._s.impact_refiner is not None:
                refined = self._s.impact_refiner.refine(segment, pose, video_path, job.handedness)
                segment = refined.segment
                notes.append(refined.note)
                if refined.source == "audio":
                    uncertainty = 1.0 / pose.fps  # el sonido fija el impacto a ±1 fotograma
        with self._obs.stage("metrics"):
            ctx = MetricContext(pose, segment, job.handedness, impact_uncertainty_s=uncertainty)
            metrics = self._s.metrics.compute_all(stroke, ctx)
        with self._obs.stage("reference"):
            comparison = self._compare(pose, segment, job, stroke)
        with self._obs.stage("rules"):
            evaluation = self._s.rules.evaluate(kb, metrics, job.camera_view)
        with self._obs.stage("prioritize"):
            findings = self._s.prioritizer.prioritize(evaluation.fired)
            strengths = insights.rank_strengths(evaluation.strengths)
        unmeasured = _labels(metrics, evaluation.unmeasured_metrics)
        warnings = [*decision.warnings, *self._s.quality.warnings(pose, job, unmeasured),
                    *_view_warning(evaluation.skipped_views)]
        diagnosis = insights.diagnose(findings, strengths, kb, unmeasured, warnings)
        report = Report(
            stroke_type=stroke,
            segment=segment,
            metrics=list(metrics.values()),
            findings=findings,
            quality=Quality(pose_detection_rate=round(pose.detection_rate, 3), warnings=warnings),
            knowledge_base_version=kb.label,
            classification=_classification(decision),
            strengths=strengths,
            diagnosis=diagnosis,
            drill_plan=insights.plan_drills(findings, diagnosis),
            reliability=insights.assess_reliability(pose.detection_rate, metrics, self._s.rules.min_confidence,
                                                    warnings, notes),
            knowledge_status=insights.knowledge_status(kb),
            reference_comparison=comparison,
        )
        return PipelineResult(report=report, pose=pose)

    def _compare(self, pose: PoseSequence, segment: Segment, job: JobMessage,
                 stroke: StrokeType) -> ReferenceComparison | None:
        """Informativa: si falla (referencia corrupta, bucket caído) el análisis sigue sin ella."""
        if self._s.comparator is None:
            return None
        try:
            return self._s.comparator.compare(pose, segment, job.handedness, stroke, job.camera_view)
        except Exception:
            logger.warning("comparación con referentes fallida; el reporte sale sin ella", exc_info=True,
                           extra={"analysis_id": job.analysis_id})
            return None

    def _estimate_pose(self, video_path: Path) -> PoseSequence:
        with self._s.video_reader.open(video_path) as stream:
            sized = _SizeRecordingStream(stream)
            pose = interpolate_gaps(self._s.pose_estimator.estimate(sized))
        return replace(smooth_pose(pose, self._s.smoothing_seconds), frame_size=sized.size)


class _SizeRecordingStream:
    """Deja pasar los fotogramas y recuerda su tamaño (ancho, alto) para el artefacto de pose."""

    def __init__(self, inner: VideoStream) -> None:
        self._inner = inner
        self.size: tuple[int, int] | None = None

    @property
    def fps(self) -> float:
        return self._inner.fps

    def frames(self) -> Iterator[Frame]:
        for frame in self._inner.frames():
            if self.size is None:
                self.size = (int(frame.shape[1]), int(frame.shape[0]))
            yield frame


def _classification(d: Decision) -> Classification:
    return Classification(stroke=d.stroke, confidence=round(d.confidence, 4), classifier_id=d.classifier_id,
                          model_version=d.model_version, dataset_hash=d.dataset_hash)


_VIEW_NAMES = {"side": "de perfil", "front": "frontal", "back": "trasera"}


def _view_warning(skipped: dict[str, set[str]]) -> list[str]:
    """Un aviso con cuántas reglas no se evaluaron por la vista de cámara y qué vista las permitiría."""
    if not skipped:
        return []
    views = sorted({v for needed in skipped.values() for v in needed})
    names = " o ".join(_VIEW_NAMES.get(v, v) for v in views)
    return [f"{len(skipped)} comprobaciones no se pueden hacer bien desde esta vista de cámara; "
            f"graba otro video con vista {names} para completarlas."]


def _labels(metrics: dict[str, MetricValue], ids: set[str]) -> list[str]:
    return [metrics[i].label if i in metrics else i for i in ids]
