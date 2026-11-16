from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tennis_worker.config import Settings
from tennis_worker.contracts import JobMessage
from tennis_worker.errors import StrokeNotRecognizedError, UnsupportedStrokeError
from tennis_worker.infrastructure.model_store import ModelStore
from tennis_worker.pipeline.classification import (
    COMBINERS,
    CascadeCombiner,
    ClassificationContext,
    ClassificationResult,
    FirstCombiner,
    PoseSequenceClassifier,
    StrokeRecognizer,
    UserProvidedClassifier,
    VerifyCombiner,
    VoteCombiner,
    YoloFrameClassifier,
)
from tennis_worker.pipeline.classification.classifiers import letterbox
from tennis_worker.pipeline.classification.features import FEATURE_SET, FEATURES_PER_FRAME, sequence_features
from tennis_worker.pipeline.classification.model import OnnxModel
from tennis_worker.pipeline.types import PoseSequence
from tennis_worker.pipeline.video import Frame, VideoStream
from tests.synthetic import make_pose
from tests.test_infrastructure import base_env

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def ctx(job: JobMessage) -> ClassificationContext:
    return ClassificationContext(make_pose(), Path("video"), job)


def model_dir(tmp_path: Path, classifier: str, **meta: Any) -> Path:
    target = tmp_path / classifier
    target.mkdir()
    shutil.copy(FIXTURES / f"{classifier}.onnx", target / "model.onnx")
    (target / "meta.json").write_text(json.dumps({"classifier": classifier, "version": "v-test", **meta}))
    return target


def pose_seq_model(tmp_path: Path, **overrides: Any) -> OnnxModel:
    meta = {"classes": ["forehand", "backhand", "other"], "feature_set": FEATURE_SET, "seq_len": 8,
            "dataset_hash": "md5:abc"} | overrides
    return OnnxModel(model_dir(tmp_path, "pose_seq", **meta), "pose_seq")


# --- características (compartidas con el entrenamiento en ml/) ---


def test_features_have_fixed_shape_whatever_the_clip_length() -> None:
    pose = make_pose()
    half = PoseSequence(pose.fps, pose.image[::2], pose.world[::2], pose.visibility[::2], pose.detected[::2])
    for clip in (pose, half):
        features = sequence_features(clip, "right", seq_len=16)
        assert features.shape == (16, FEATURES_PER_FRAME) and features.dtype == np.float32


def test_features_are_scale_invariant() -> None:
    pose = make_pose()
    bigger = replace(pose, world=pose.world * 1.8)  # jugador más alto / más cerca
    np.testing.assert_allclose(sequence_features(pose, "right", 16), sequence_features(bigger, "right", 16), atol=1e-5)


def test_left_handed_player_is_mirrored_to_right_handed() -> None:
    pose = make_pose()
    mirrored_world = pose.world.copy()
    mirrored_world[..., 0] *= -1
    pairs = [(11, 12), (13, 14), (15, 16), (23, 24), (25, 26), (27, 28), (1, 4), (2, 5), (3, 6), (7, 8),
             (9, 10), (17, 18), (19, 20), (21, 22), (29, 30), (31, 32)]
    for a, b in pairs:
        mirrored_world[:, [a, b]] = mirrored_world[:, [b, a]]
    lefty = replace(pose, world=mirrored_world)
    np.testing.assert_allclose(sequence_features(lefty, "left", 16), sequence_features(pose, "right", 16), atol=1e-5)


def test_features_of_empty_sequence_are_zeros() -> None:
    empty = PoseSequence(30.0, np.zeros((0, 33, 3)), np.zeros((0, 33, 3)), np.zeros((0, 33)), np.zeros(0, dtype=bool))
    assert not sequence_features(empty, "right", 8).any()


# --- clasificadores ---


def test_user_classifier_abstains_when_stroke_not_declared(ctx: ClassificationContext) -> None:
    assert UserProvidedClassifier().classify(ctx) == ClassificationResult("user", {"forehand": 1.0})
    undeclared = replace(ctx, job=ctx.job.model_copy(update={"stroke_type": None}))
    assert UserProvidedClassifier().classify(undeclared) is None


def test_pose_sequence_classifier_maps_softmax_to_classes(tmp_path: Path, ctx: ClassificationContext) -> None:
    result = PoseSequenceClassifier(pose_seq_model(tmp_path)).classify(ctx)
    expected = np.exp([3.0, 0.0, 0.0]) / np.exp([3.0, 0.0, 0.0]).sum()
    assert result.top[0] == "forehand"
    np.testing.assert_allclose(list(result.probabilities.values()), expected, rtol=1e-5)
    assert (result.model_version, result.dataset_hash) == ("v-test", "md5:abc")


def test_pose_sequence_refuses_model_trained_with_other_features(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="feature_set"):
        PoseSequenceClassifier(pose_seq_model(tmp_path, feature_set="otro_v0"))


def test_model_output_must_match_declared_classes(tmp_path: Path, ctx: ClassificationContext) -> None:
    classifier = PoseSequenceClassifier(pose_seq_model(tmp_path, classes=["forehand", "backhand"]))
    with pytest.raises(ValueError):
        classifier.classify(ctx)


def test_onnx_model_fails_fast_on_missing_or_mismatched_artifacts(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        OnnxModel(tmp_path, "pose_seq")
    directory = model_dir(tmp_path, "yolo", classes=["forehand", "serve"], imgsz=32, frames=4)
    with pytest.raises(ValueError, match="no 'pose_seq'"):
        OnnxModel(directory, "pose_seq")


class FramesReader:
    def __init__(self, count: int) -> None:
        self.count = count

    @contextmanager
    def open(self, path: Path) -> Iterator[VideoStream]:
        yield FramesStream(self.count)


class FramesStream:
    fps = 30.0

    def __init__(self, count: int) -> None:
        self.count = count

    def frames(self) -> Iterator[Frame]:
        return (np.full((48, 64, 3), i, dtype=np.uint8) for i in range(self.count))


def test_yolo_classifier_takes_max_per_class_and_averages_frames(tmp_path: Path, ctx: ClassificationContext) -> None:
    model = OnnxModel(model_dir(tmp_path, "yolo", classes=["forehand", "serve"], imgsz=32, frames=4), "yolo")
    result = YoloFrameClassifier(model, FramesReader(ctx.pose.frame_count)).classify(ctx)
    assert result.probabilities == pytest.approx({"forehand": 0.8, "serve": 0.3})


def test_letterbox_keeps_aspect_and_pads_with_grey() -> None:
    out = letterbox(np.zeros((48, 64, 3), dtype=np.uint8), 32)
    assert out.shape == (3, 32, 32) and out.max() <= 1.0
    assert out[0, 0, 0] == pytest.approx(114 / 255) and out[0, 16, 16] == 0.0


# --- combinadores ---


class Fixed:
    def __init__(self, cid: str, probabilities: dict[str, float] | None) -> None:
        self.id = cid
        self.probabilities = probabilities
        self.calls = 0

    def classify(self, ctx: ClassificationContext) -> ClassificationResult | None:
        self.calls += 1
        return None if self.probabilities is None else ClassificationResult(self.id, self.probabilities, "v1")


def test_first_uses_first_non_abstaining_classifier(ctx: ClassificationContext) -> None:
    decision = FirstCombiner(0.7).decide([Fixed("user", None), Fixed("pose_seq", {"serve": 0.9})], ctx)
    assert (decision.stroke, decision.classifier_id) == ("serve", "pose_seq")
    with pytest.raises(StrokeNotRecognizedError):
        FirstCombiner(0.7).decide([Fixed("pose_seq", {"serve": 0.5, "forehand": 0.5})], ctx)


def test_cascade_stops_at_first_confident_and_rejects_unknown_classes(ctx: ClassificationContext) -> None:
    unsure, sure, expensive = Fixed("a", {"forehand": 0.6}), Fixed("b", {"forehand": 0.95}), Fixed("c", {})
    assert CascadeCombiner(0.7).decide([unsure, sure, expensive], ctx).classifier_id == "b"
    assert expensive.calls == 0  # no se paga la inferencia que no hace falta
    with pytest.raises(StrokeNotRecognizedError):
        CascadeCombiner(0.7).decide([Fixed("a", {"other": 0.99})], ctx)  # smash: no es un golpe soportado


def test_vote_weights_classifiers(ctx: ClassificationContext) -> None:
    pose, yolo = Fixed("pose_seq", {"forehand": 0.9, "serve": 0.1}), Fixed("yolo", {"forehand": 0.2, "serve": 0.8})
    assert VoteCombiner(0.5, {"pose_seq": 3}).decide([pose, yolo], ctx).stroke == "forehand"
    assert VoteCombiner(0.5, {"yolo": 3}).decide([pose, yolo], ctx).stroke == "serve"
    with pytest.raises(StrokeNotRecognizedError):
        VoteCombiner(0.5, {}).decide([Fixed("user", None)], ctx)


def test_verify_keeps_declared_stroke_and_warns_on_confident_disagreement(ctx: ClassificationContext) -> None:
    decision = VerifyCombiner(0.7).decide([UserProvidedClassifier(), Fixed("pose_seq", {"serve": 0.9})], ctx)
    assert decision.stroke == "forehand" and decision.classifier_id == "user"
    assert len(decision.warnings) == 1 and "serve" in decision.warnings[0]
    quiet = VerifyCombiner(0.7).decide([UserProvidedClassifier(), Fixed("pose_seq", {"serve": 0.6})], ctx)
    assert quiet.warnings == ()


def test_verify_lets_models_decide_when_user_did_not_declare(ctx: ClassificationContext) -> None:
    undeclared = replace(ctx, job=ctx.job.model_copy(update={"stroke_type": None}))
    decision = VerifyCombiner(0.7).decide([UserProvidedClassifier(), Fixed("pose_seq", {"forehand": 0.8})], undeclared)
    assert (decision.stroke, decision.classifier_id) == ("forehand", "pose_seq")


def test_every_combiner_is_registered_and_recognizer_needs_classifiers(ctx: ClassificationContext) -> None:
    assert set(COMBINERS) == {"first", "cascade", "vote", "verify"}
    recognizer = StrokeRecognizer([UserProvidedClassifier()], COMBINERS["first"](0.7, {}))
    assert recognizer.recognize(ctx).stroke == "forehand"
    with pytest.raises(ValueError):
        StrokeRecognizer([], FirstCombiner(0.7))


def test_not_recognized_keeps_the_existing_error_code() -> None:
    assert StrokeNotRecognizedError.code == UnsupportedStrokeError.code == "UNSUPPORTED_STROKE"
    assert StrokeNotRecognizedError.user_message != UnsupportedStrokeError.user_message


# --- almacén de modelos y configuración ---


class FakeDownloader:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def download(self, key: str, destination: Path) -> None:
        self.keys.append(key)
        destination.write_bytes(b"x")


def test_model_store_downloads_versioned_models_once(tmp_path: Path) -> None:
    downloader = FakeDownloader()
    store = ModelStore(downloader, tmp_path)
    path = store.ensure("pose_seq", "v3")
    store.ensure("pose_seq", "v3")
    assert path == tmp_path / "pose_seq" / "v3"
    assert downloader.keys == ["models/pose_seq/v3/meta.json", "models/pose_seq/v3/model.onnx"]
    assert store.ensure("yolo", "") == tmp_path / "yolo" and len(downloader.keys) == 2


def test_settings_parse_classifiers_and_weights(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = base_env(tmp_path) | {"STROKE_CLASSIFIERS": " user, pose_seq ", "STROKE_COMBINER": "vote",
                                "STROKE_VOTE_WEIGHTS": "pose_seq=2,yolo=0.5"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    settings = Settings()  # type: ignore[call-arg]
    assert settings.classifier_ids == ["user", "pose_seq"]
    assert settings.vote_weights == {"pose_seq": 2.0, "yolo": 0.5}


@pytest.mark.parametrize(("key", "value"), [("STROKE_CLASSIFIERS", "user,magic"), ("STROKE_VOTE_WEIGHTS", "pose_seq"),
                                            ("STROKE_COMBINER", "random")])
def test_settings_reject_invalid_classifier_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   key: str, value: str) -> None:
    for k, v in (base_env(tmp_path) | {key: value}).items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError):
        Settings()  # type: ignore[call-arg]
