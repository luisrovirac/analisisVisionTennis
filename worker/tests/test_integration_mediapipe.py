"""Pruebas con el modelo real de MediaPipe.

Requisitos (no versionados en git):
  - models/pose_landmarker_full.task   (ver README)
  - samples/forehand.mp4               (forehand real grabado de perfil) para la prueba completa
Ejecutar: pytest -m integration
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from tennis_worker.contracts import JobMessage
from tennis_worker.errors import AnalysisError
from tennis_worker.pipeline.biomechanics.registry import default_registry
from tennis_worker.pipeline.classification import FirstCombiner, StrokeRecognizer, UserProvidedClassifier
from tennis_worker.pipeline.knowledge.engine import RuleEngine
from tennis_worker.pipeline.knowledge.repository import YamlKnowledgeBaseRepository
from tennis_worker.pipeline.orchestrator import AnalysisPipeline, PipelineStages
from tennis_worker.pipeline.prioritization import CategoryImpactPrioritizer
from tennis_worker.pipeline.quality import QualityAssessor
from tennis_worker.pipeline.segmentation import WristSpeedSegmenter
from tennis_worker.pipeline.video import OpenCVVideoReader
from tests.conftest import KNOWLEDGE_DIR, WORKER_ROOT

PROJECT_ROOT = WORKER_ROOT.parent
MODEL = PROJECT_ROOT / "models" / "pose_landmarker_full.task"
SAMPLE = PROJECT_ROOT / "samples" / "forehand.mp4"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not MODEL.exists(), reason=f"falta el modelo {MODEL}"),
]


def real_pipeline() -> AnalysisPipeline:
    from tennis_worker.pipeline.pose.mediapipe_estimator import MediaPipePoseEstimator

    return AnalysisPipeline(PipelineStages(
        video_reader=OpenCVVideoReader(max_seconds=30),
        pose_estimator=MediaPipePoseEstimator(MODEL),
        quality=QualityAssessor(min_detection_rate=0.6),
        classifier=StrokeRecognizer([UserProvidedClassifier()], FirstCombiner(0.7)),
        segmenter=WristSpeedSegmenter(),
        metrics=default_registry(),
        knowledge=YamlKnowledgeBaseRepository(KNOWLEDGE_DIR),
        rules=RuleEngine(min_confidence=0.5),
        prioritizer=CategoryImpactPrioritizer(),
    ))


def test_empty_scene_reports_no_person(tmp_path: Path, job: JobMessage) -> None:
    """Verifica que MediaPipe carga y corre en esta plataforma."""
    video = tmp_path / "empty.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 30, (320, 240))
    for _ in range(15):
        writer.write(np.full((240, 320, 3), 40, dtype=np.uint8))
    writer.release()
    with pytest.raises(AnalysisError) as exc_info:
        real_pipeline().run(video, job)
    assert exc_info.value.code == "NO_PERSON_DETECTED"


@pytest.mark.skipif(not SAMPLE.exists(), reason=f"falta el video de muestra {SAMPLE}")
def test_real_forehand_produces_report(job: JobMessage) -> None:
    result = real_pipeline().run(SAMPLE, job)
    report = result.report
    assert report.quality.pose_detection_rate > 0.6
    assert {m.id for m in report.metrics} == default_registry().metric_ids("forehand")
    print("\n" + report.model_dump_json(indent=2))  # útil para calibrar umbrales a mano
