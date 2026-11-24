from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tennis_worker.contracts import JobMessage, StrokeType
from tennis_worker.errors import StrokeNotRecognizedError, UnsupportedStrokeError
from tennis_worker.pipeline.biomechanics.registry import default_registry
from tennis_worker.pipeline.classification import (
    CascadeCombiner,
    ClassificationContext,
    ClassificationResult,
    FirstCombiner,
    StrokeRecognizer,
    UserProvidedClassifier,
    VerifyCombiner,
)
from tennis_worker.pipeline.knowledge.engine import RuleEngine
from tennis_worker.pipeline.knowledge.repository import YamlKnowledgeBaseRepository
from tennis_worker.pipeline.orchestrator import AnalysisPipeline, PipelineStages
from tennis_worker.pipeline.prioritization import CategoryImpactPrioritizer
from tennis_worker.pipeline.quality import QualityAssessor
from tennis_worker.pipeline.reference import ReferenceComparator, ReferenceStroke
from tennis_worker.pipeline.segmentation import WristSpeedSegmenter
from tennis_worker.pipeline.types import PoseSequence
from tennis_worker.pipeline.video import Frame, VideoStream
from tests.conftest import CONTRACTS_DIR, KNOWLEDGE_DIR
from tests.synthetic import BAD_SWING, GOOD_SWING, SwingSpec, make_pose


class FakeStream:
    fps = 60.0

    def frames(self) -> Iterator[Frame]:
        return iter([np.zeros((360, 640, 3), dtype=np.uint8)])


class FakeReader:
    @contextmanager
    def open(self, path: Path) -> Iterator[VideoStream]:
        yield FakeStream()


class FakeEstimator:
    def __init__(self, pose: PoseSequence) -> None:
        self._pose = pose

    def estimate(self, stream: VideoStream) -> PoseSequence:
        for _ in stream.frames():  # consume el video como un estimador real
            pass
        return self._pose


def pipeline_for(spec: SwingSpec) -> AnalysisPipeline:
    return AnalysisPipeline(stages_for(spec))


def stages_for(spec: SwingSpec) -> PipelineStages:
    return PipelineStages(
        video_reader=FakeReader(),
        pose_estimator=FakeEstimator(make_pose(spec)),
        quality=QualityAssessor(min_detection_rate=0.6),
        classifier=StrokeRecognizer([UserProvidedClassifier()], FirstCombiner(0.7)),
        segmenter=WristSpeedSegmenter(),
        metrics=default_registry(),
        knowledge=YamlKnowledgeBaseRepository(KNOWLEDGE_DIR),
        rules=RuleEngine(min_confidence=0.5),
        prioritizer=CategoryImpactPrioritizer(),
    )


def test_good_swing_produces_no_findings(job: JobMessage) -> None:
    report = pipeline_for(GOOD_SWING).run(Path("video"), job).report
    assert report.findings == []
    assert report.knowledge_base_version.startswith("forehand@")
    assert report.quality.pose_detection_rate == 1.0


def test_bad_swing_lists_errors_fundamental_first(job: JobMessage) -> None:
    report = pipeline_for(BAD_SWING).run(Path("video"), job).report
    ids = [f.rule_id for f in report.findings]
    assert {"FH-PREP-001", "FH-POST-001", "FH-CONT-001", "FH-FIN-001", "FH-SYNC-002", "FH-SYNC-003"} <= set(ids)
    categories = [f.category for f in report.findings]
    assert categories == sorted(categories, key=lambda c: c != "fundamental")
    assert [f.rank for f in report.findings] == list(range(1, len(ids) + 1))


def test_report_validates_against_json_schema(job: JobMessage) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((CONTRACTS_DIR / "report.schema.json").read_text(encoding="utf-8"))
    report = pipeline_for(BAD_SWING).run(Path("video"), job).report
    jsonschema.validate(report.model_dump(mode="json"), schema)


def test_unsupported_stroke_fails_with_actionable_error(job: JobMessage, tmp_path: Path) -> None:
    no_knowledge = AnalysisPipeline(replace(stages_for(GOOD_SWING), knowledge=YamlKnowledgeBaseRepository(tmp_path)))
    with pytest.raises(UnsupportedStrokeError):
        no_knowledge.run(Path("video"), job.model_copy(update={"stroke_type": "serve"}))


@pytest.mark.parametrize("stroke", ["backhand", "serve", "volley"])
def test_new_strokes_produce_a_valid_report(job: JobMessage, stroke: StrokeType) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    report = pipeline_for(BAD_SWING).run(Path("video"), job.model_copy(update={"stroke_type": stroke})).report
    assert report.stroke_type == stroke and report.knowledge_base_version.startswith(f"{stroke}@")
    assert {m.id for m in report.metrics} == default_registry().metric_ids(stroke)
    schema = json.loads((CONTRACTS_DIR / "report.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(report.model_dump(mode="json"), schema)


class Disagreeing:
    id = "pose_seq"

    def classify(self, ctx: ClassificationContext) -> ClassificationResult:
        return ClassificationResult(self.id, {"serve": 0.92, "forehand": 0.08}, "pose_seq@v7", "md5:data")


def pipeline_with(recognizer: StrokeRecognizer) -> AnalysisPipeline:
    return AnalysisPipeline(replace(stages_for(GOOD_SWING), classifier=recognizer))


def test_report_records_how_the_stroke_was_decided(job: JobMessage) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    recognizer = StrokeRecognizer([UserProvidedClassifier(), Disagreeing()], VerifyCombiner(0.7))
    report = pipeline_with(recognizer).run(Path("video"), job).report
    assert report.classification is not None and report.classification.classifier_id == "user"
    assert report.stroke_type == "forehand" and any("serve" in w for w in report.quality.warnings)
    schema = json.loads((CONTRACTS_DIR / "report.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(report.model_dump(mode="json"), schema)


def test_detected_stroke_is_used_when_user_did_not_declare(job: JobMessage) -> None:
    undeclared = job.model_copy(update={"stroke_type": None, "schema_version": "1.1"})
    recognizer = StrokeRecognizer([UserProvidedClassifier(), Confident()], CascadeCombiner(0.7))
    report = pipeline_with(recognizer).run(Path("video"), undeclared).report
    assert report.stroke_type == "forehand"
    assert report.classification is not None
    assert (report.classification.model_version, report.classification.dataset_hash) == ("pose_seq@v7", "md5:data")


def test_unrecognized_stroke_fails_with_actionable_error(job: JobMessage) -> None:
    undeclared = job.model_copy(update={"stroke_type": None})
    recognizer = StrokeRecognizer([UserProvidedClassifier()], FirstCombiner(0.7))
    with pytest.raises(StrokeNotRecognizedError):
        pipeline_with(recognizer).run(Path("video"), undeclared)


class Confident:
    id = "pose_seq"

    def classify(self, ctx: ClassificationContext) -> ClassificationResult:
        return ClassificationResult(self.id, {"forehand": 0.9, "serve": 0.1}, "pose_seq@v7", "md5:data")


def test_good_swing_reports_strengths_and_1_1_fields(job: JobMessage) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    report = pipeline_for(GOOD_SWING).run(Path("video"), job).report
    assert 1 <= len(report.strengths) <= 5
    assert [s.rank for s in report.strengths] == list(range(1, len(report.strengths) + 1))
    assert report.diagnosis is not None and report.diagnosis.root_cause_rule_id is None
    assert report.drill_plan is None  # sin errores no hay plan
    assert report.reliability is not None and report.knowledge_status is not None
    assert report.knowledge_status.status == "provisional"  # la semilla no está validada
    schema = json.loads((CONTRACTS_DIR / "report.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(report.model_dump(mode="json"), schema)


def test_bad_swing_diagnosis_names_a_root_cause_and_plan(job: JobMessage) -> None:
    report = pipeline_for(BAD_SWING).run(Path("video"), job).report
    diagnosis = report.diagnosis
    assert diagnosis is not None and diagnosis.root_cause_rule_id == "FH-PREP-001"
    assert {"FH-CONT-001", "FH-FIN-001"} <= set(diagnosis.contributing_factors)  # PREP → CONT → FIN
    assert report.drill_plan is not None and report.drill_plan.items[0].focus_rule_id == "FH-PREP-001"
    assert not any(s.rule_id in {"FH-STR-003", "FH-STR-007"} for s in report.strengths)  # suprimidas


def test_pose_records_the_analyzed_frame_size(job: JobMessage) -> None:
    pose = pipeline_for(GOOD_SWING).run(Path("video"), job).pose
    assert pose.frame_size == (640, 360)


class _OneReference:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def references(self, stroke: StrokeType, view: str) -> list[ReferenceStroke]:
        if self.fail:
            raise RuntimeError("bucket caído")
        pose = make_pose(GOOD_SWING)
        return [ReferenceStroke(ref_id="pro", label="Modelo", stroke=stroke, camera_view="side", handedness="right",
                                source="test", pose=pose, segment=WristSpeedSegmenter().segment(pose, "right"))]


def test_report_includes_informative_reference_comparison(job: JobMessage) -> None:
    stages = replace(stages_for(BAD_SWING), comparator=ReferenceComparator(_OneReference()))
    report = AnalysisPipeline(stages).run(Path("video"), job).report
    plain = pipeline_for(BAD_SWING).run(Path("video"), job).report
    assert report.reference_comparison is not None and report.reference_comparison.status == "ok"
    assert report.findings == plain.findings and report.strengths == plain.strengths  # no altera el diagnóstico
    assert plain.reference_comparison is None


def test_reference_failure_does_not_break_the_analysis(job: JobMessage) -> None:
    stages = replace(stages_for(GOOD_SWING), comparator=ReferenceComparator(_OneReference(fail=True)))
    report = AnalysisPipeline(stages).run(Path("video"), job).report
    assert report.reference_comparison is None and report.metrics
