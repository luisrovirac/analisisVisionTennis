"""Orquestador: compone los pasos del pipeline, todos inyectados (sin lógica de dominio propia)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tennis_worker.contracts import Classification, JobMessage, MetricValue, Quality, Report
from tennis_worker.observability import Instrumentation
from tennis_worker.pipeline.biomechanics.base import MetricContext
from tennis_worker.pipeline.biomechanics.registry import MetricRegistry
from tennis_worker.pipeline.classification import ClassificationContext, Decision, StrokeRecognizer
from tennis_worker.pipeline.knowledge.engine import RuleEngine
from tennis_worker.pipeline.knowledge.repository import KnowledgeBaseRepository
from tennis_worker.pipeline.pose import PoseEstimator
from tennis_worker.pipeline.pose.postprocess import interpolate_gaps
from tennis_worker.pipeline.prioritization import Prioritizer
from tennis_worker.pipeline.quality import QualityAssessor
from tennis_worker.pipeline.segmentation import StrokeSegmenter
from tennis_worker.pipeline.types import PoseSequence
from tennis_worker.pipeline.video import VideoReader


@dataclass(frozen=True)
class PipelineStages:
    video_reader: VideoReader
    pose_estimator: PoseEstimator
    quality: QualityAssessor
    classifier: StrokeRecognizer
    segmenter: StrokeSegmenter
    metrics: MetricRegistry
    knowledge: KnowledgeBaseRepository
    rules: RuleEngine
    prioritizer: Prioritizer


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
        with self._obs.stage("metrics"):
            metrics = self._s.metrics.compute_all(stroke, MetricContext(pose, segment, job.handedness))
        with self._obs.stage("rules"):
            evaluation = self._s.rules.evaluate(kb, metrics)
        with self._obs.stage("prioritize"):
            findings = self._s.prioritizer.prioritize(evaluation.fired)
        report = Report(
            stroke_type=stroke,
            segment=segment,
            metrics=list(metrics.values()),
            findings=findings,
            quality=Quality(
                pose_detection_rate=round(pose.detection_rate, 3),
                warnings=[*decision.warnings,
                          *self._s.quality.warnings(pose, job, _labels(metrics, evaluation.unmeasured_metrics))],
            ),
            knowledge_base_version=kb.label,
            classification=_classification(decision),
        )
        return PipelineResult(report=report, pose=pose)

    def _estimate_pose(self, video_path: Path) -> PoseSequence:
        with self._s.video_reader.open(video_path) as stream:
            return interpolate_gaps(self._s.pose_estimator.estimate(stream))


def _classification(d: Decision) -> Classification:
    return Classification(stroke=d.stroke, confidence=round(d.confidence, 4), classifier_id=d.classifier_id,
                          model_version=d.model_version, dataset_hash=d.dataset_hash)


def _labels(metrics: dict[str, MetricValue], ids: set[str]) -> list[str]:
    return [metrics[i].label if i in metrics else i for i in ids]
