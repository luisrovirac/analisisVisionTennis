"""Fase 2: RTMPose (2D) + MotionBERT (3D). Se prueban la geometría y el ensamblado con sesiones ONNX falsas;
los modelos reales se comparan con scripts/compare_pose.py."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tennis_worker.infrastructure.pose_models import PinnedModel, ensure_onnx, sha256_of
from tennis_worker.pipeline.landmarks import LEFT, RIGHT, Landmark
from tennis_worker.pipeline.pose.lifting import (
    H36M_LEFT,
    H36M_RIGHT,
    MotionBertLifter,
    coco_to_h36m,
    flip_h36m,
    normalize_pixels,
    to_metres,
)
from tennis_worker.pipeline.pose.rtm_motionbert_estimator import RtmPoseMotionBertEstimator, Tracks2D
from tennis_worker.pipeline.pose.rtmpose import (
    Box,
    PersonTracker,
    RtmPoseModel,
    YoloxPersonDetector,
    box_center_scale,
    crop_matrix,
    decode_simcc,
    letterbox,
)
from tennis_worker.pipeline.video import Frame

# --- Geometría 2D ------------------------------------------------------------------------------------


def test_box_iou_and_area() -> None:
    a, b = Box(0, 0, 10, 10, 0.9), Box(5, 0, 15, 10, 0.9)
    assert a.area == 100 and a.iou(b) == pytest.approx(50 / 150)
    assert a.iou(Box(20, 20, 30, 30, 0.9)) == 0.0


def test_letterbox_keeps_aspect_and_pads_with_114() -> None:
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    padded, ratio = letterbox(frame, 640)
    assert padded.shape == (640, 640, 3) and ratio == 1.0
    assert (padded[360:] == 114).all() and (padded[:360] == 0).all()


def test_center_scale_matches_model_aspect() -> None:
    center, scale = box_center_scale(Box(100, 50, 200, 450, 0.9), aspect=192 / 256)
    assert center.tolist() == [150, 250]
    assert scale[0] / scale[1] == pytest.approx(192 / 256)
    assert scale[1] == pytest.approx(400 * 1.25)  # caja alta: manda la altura con el margen


def test_crop_matrix_maps_the_box_onto_the_model_input() -> None:
    center, scale = np.array([150.0, 250.0]), np.array([375.0, 500.0])
    matrix = crop_matrix(center, scale, 192, 256)
    corners = np.array([[center[0] - scale[0] / 2, center[1] - scale[1] / 2, 1], [*center, 1]])
    mapped = corners @ matrix.T
    assert mapped[0] == pytest.approx([0, 0]) and mapped[1] == pytest.approx([96, 128])


def test_decode_simcc_takes_the_peak_and_halves_it() -> None:
    simcc_x, simcc_y = np.zeros((2, 384)), np.zeros((2, 512))
    simcc_x[0, 100], simcc_y[0, 300] = 0.8, 0.6
    simcc_x[1, 10], simcc_y[1, 20] = 0.2, 0.4
    locs, scores = decode_simcc(simcc_x, simcc_y)
    assert locs.tolist() == [[50, 150], [5, 10]]
    assert scores == pytest.approx([0.7, 0.3])


def test_tracker_follows_the_player_and_starts_with_the_biggest_person() -> None:
    tracker = PersonTracker()
    player, other = Box(100, 100, 300, 500, 0.9), Box(600, 200, 650, 300, 0.95)
    assert tracker.select([other, player]) == player  # el más grande
    moved = Box(110, 100, 310, 500, 0.8)
    assert tracker.select([other, moved]) == moved  # el que más solapa con el anterior
    assert tracker.select([]) is None


# --- Modelos con sesiones falsas ---------------------------------------------------------------------


@dataclass
class FakeSession:
    outputs: list[Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def run(self, output_names: Sequence[str] | None, input_feed: dict[str, Any]) -> list[Any]:
        self.calls.append(input_feed)
        return self.outputs


def test_detector_scales_boxes_back_and_keeps_only_people() -> None:
    dets = np.array([[[10, 20, 110, 220, 0.9], [0, 0, 50, 50, 0.95], [5, 5, 6, 6, 0.1]]])
    session = FakeSession([dets, np.array([[0, 2, 0]])])
    boxes = YoloxPersonDetector(session).detect(np.zeros((1280, 1280, 3), dtype=np.uint8))  # ratio 0,5
    assert boxes == [Box(20, 40, 220, 440, pytest.approx(0.9))]
    assert session.calls[0]["input"].shape == (1, 3, 640, 640)


def test_pose_model_maps_keypoints_back_to_the_image() -> None:
    simcc_x, simcc_y = np.zeros((1, 17, 384)), np.zeros((1, 17, 512))
    simcc_x[0, :, 192], simcc_y[0, :, 256] = 1.0, 1.0  # centro del recorte
    session = FakeSession([simcc_x, simcc_y])
    points, scores = RtmPoseModel(session).estimate(np.zeros((720, 1280, 3), dtype=np.uint8),
                                                    Box(100, 50, 200, 450, 0.9))
    assert points == pytest.approx(np.tile([150, 250], (17, 1)))
    assert scores == pytest.approx(np.ones(17))
    assert session.calls[0]["input"].shape == (1, 3, 256, 192)


# --- Lifting --------------------------------------------------------------------------------------------


def coco_pose(frames: int = 3) -> tuple[np.ndarray, np.ndarray]:
    points = np.zeros((frames, 17, 2))
    points[:, 5], points[:, 6] = [80, 100], [120, 100]  # hombros
    points[:, 11], points[:, 12] = [90, 200], [110, 200]  # caderas
    points[:, 0], points[:, 1], points[:, 2] = [100, 60], [95, 55], [105, 55]
    scores = np.full((frames, 17), 0.9)
    scores[:, 12] = 0.4
    return points, scores


def test_coco_to_h36m_builds_the_midline() -> None:
    h36m = coco_to_h36m(*coco_pose())
    assert h36m[0, 0].tolist() == [100, 200, 0.4]  # pelvis = punto medio, confianza de la peor cadera
    assert h36m[0, 8, :2].tolist() == [100, 100]  # tórax = punto medio de los hombros
    assert h36m[0, 7, :2].tolist() == [100, 150]  # columna
    assert h36m[0, 1, :2].tolist() == [110, 200] and h36m[0, 4, :2].tolist() == [90, 200]
    assert h36m[0, 10, 1] < h36m[0, 9, 1]  # la cabeza queda por encima de la nariz


def test_normalize_pixels_centres_on_the_image() -> None:
    data = np.array([[[640.0, 360.0, 0.9], [1000.0, 360.0, 0.5]]])
    out = normalize_pixels(data, 1280, 720)
    assert out[0, 0].tolist() == [0, 0, 0.9]
    assert out[0, 1, :2].tolist() == pytest.approx([1.0, 0.0])


def test_flip_is_an_involution_that_swaps_sides() -> None:
    data = np.random.default_rng(1).normal(size=(4, 17, 3))
    flipped = flip_h36m(data)
    assert np.allclose(flip_h36m(flipped), data)
    assert np.allclose(flipped[:, H36M_LEFT[0], 0], -data[:, H36M_RIGHT[0], 0])


class IdentityLifter:
    """Sesión falsa: devuelve la entrada (x, y, confianza) como si fuera 3D."""

    def __init__(self) -> None:
        self.shapes: list[tuple[int, ...]] = []

    def run(self, output_names: Sequence[str] | None, input_feed: dict[str, Any]) -> list[Any]:
        self.shapes.append(input_feed["input"].shape)
        return [input_feed["input"]]


def test_lifter_splits_long_sequences_into_padded_clips() -> None:
    session = IdentityLifter()
    data = np.random.default_rng(2).normal(size=(300, 17, 3))
    out = MotionBertLifter(session, clip_len=243, flip=False).lift(data)
    assert out.shape == data.shape and np.allclose(out, data, atol=1e-6)
    assert session.shapes == [(1, 243, 17, 3), (1, 243, 17, 3)]


def test_lifter_flip_averaging_is_neutral_for_a_symmetric_model() -> None:
    data = np.random.default_rng(3).normal(size=(10, 17, 3))
    out = MotionBertLifter(IdentityLifter(), clip_len=243, flip=True).lift(data)
    assert np.allclose(out, data, atol=1e-6)


def test_to_metres_centres_on_the_pelvis_and_scales_the_torso() -> None:
    camera = np.zeros((2, 17, 3))
    camera[:, 0] = [1.0, 1.0, 5.0]
    camera[:, 8] = [1.0, 0.8, 5.0]  # tórax 0,2 unidades por encima
    metres = to_metres(camera, torso_metres=0.5)
    assert metres[0, 0].tolist() == [0, 0, 0]
    assert metres[0, 8].tolist() == pytest.approx([0, -0.5, 0])


# --- Estimador completo ------------------------------------------------------------------------------


def test_build_fills_the_mediapipe_layout() -> None:
    points, scores = coco_pose(frames=4)
    detected = np.array([True, False, True, True])
    tracks = Tracks2D(points, scores, detected, width=200, height=300)
    estimator = RtmPoseMotionBertEstimator(detector=None, pose_model=None,  # type: ignore[arg-type]
                                           lifter=MotionBertLifter(IdentityLifter(), flip=False))
    pose = estimator.build(tracks, fps=60.0)
    assert pose.world.shape == (4, 33, 3) and pose.detected.tolist() == detected.tolist()
    assert pose.image[0, LEFT.shoulder, :2].tolist() == pytest.approx([80 / 200, 100 / 300])
    assert pose.visibility[0, RIGHT.hip] == pytest.approx(0.4)
    assert pose.visibility[0, 2] == 0.0  # ojos: sin 3D real, no suman confianza
    assert pose.world[0, LEFT.hip, :2] == pytest.approx(-pose.world[0, RIGHT.hip, :2])  # centrado en la pelvis
    assert not np.isnan(pose.world).any()
    torso = np.linalg.norm((pose.world[0, LEFT.shoulder] + pose.world[0, RIGHT.shoulder]) / 2)
    assert torso == pytest.approx(0.5)
    assert np.allclose(pose.world[:, Landmark.NOSE], pose.world[:, 2])


def test_build_without_detections_returns_an_empty_pose() -> None:
    points, scores = coco_pose(frames=3)
    tracks = Tracks2D(points, scores, np.zeros(3, dtype=bool), 200, 300)
    estimator = RtmPoseMotionBertEstimator(None, None, MotionBertLifter(IdentityLifter()))  # type: ignore[arg-type]
    assert not estimator.build(tracks, 30.0).detected.any()


@dataclass
class OneBoxDetector:
    calls: int = 0

    def detect(self, frame: Frame) -> list[Box]:
        self.calls += 1
        return [Box(50, 50, 150, 250, 0.9)]


@dataclass
class FixedPose:
    score: float = 0.9

    def estimate(self, frame: Frame, box: Box) -> tuple[np.ndarray, np.ndarray]:
        points = np.column_stack([np.linspace(box.x1, box.x2, 17), np.linspace(box.y1, box.y2, 17)])
        return points, np.full(17, self.score)


@dataclass
class FakeStream:
    count: int
    fps: float = 30.0

    def frames(self) -> Iterator[Frame]:
        for _ in range(self.count):
            yield np.zeros((300, 200, 3), dtype=np.uint8)


def test_tracking_reuses_the_previous_pose_between_detections() -> None:
    detector = OneBoxDetector()
    estimator = RtmPoseMotionBertEstimator(detector, FixedPose(), MotionBertLifter(IdentityLifter()),  # type: ignore[arg-type]
                                           detect_every=5)
    tracks = estimator.track_2d(FakeStream(12))
    assert tracks.detected.all() and detector.calls == 3  # fotogramas 0, 5 y 10
    assert (tracks.width, tracks.height) == (200, 300)


def test_low_pose_scores_count_as_no_player() -> None:
    estimator = RtmPoseMotionBertEstimator(OneBoxDetector(), FixedPose(score=0.1),  # type: ignore[arg-type]
                                           MotionBertLifter(IdentityLifter()))
    tracks = estimator.track_2d(FakeStream(4))
    assert not tracks.detected.any() and (tracks.scores == 0).all()


# --- Descarga verificada de modelos --------------------------------------------------------------------


def zip_with_model(tmp_path: Path) -> Path:
    import zipfile

    archive = tmp_path / "model.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("x/y/end2end.onnx", b"onnx-bytes")
    return archive


def test_ensure_onnx_downloads_verifies_and_caches(tmp_path: Path) -> None:
    archive = zip_with_model(tmp_path)
    model = PinnedModel("m", archive.as_uri(), sha256_of(archive))
    path = ensure_onnx(model, tmp_path / "cache")
    assert path.read_bytes() == b"onnx-bytes"
    archive.unlink()  # segunda vez: no vuelve a descargar
    assert ensure_onnx(model, tmp_path / "cache") == path


def test_ensure_onnx_rejects_a_tampered_file(tmp_path: Path) -> None:
    archive = zip_with_model(tmp_path)
    model = PinnedModel("m", archive.as_uri(), hashlib.sha256(b"otro").hexdigest())
    with pytest.raises(RuntimeError, match="SHA-256"):
        ensure_onnx(model, tmp_path / "cache")
    assert not (tmp_path / "cache" / "rtmpose" / "m" / "end2end.onnx").exists()
