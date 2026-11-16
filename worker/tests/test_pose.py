from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from tennis_worker.pipeline.pose.builder import PoseSequenceBuilder
from tennis_worker.pipeline.pose.postprocess import interpolate_gaps
from tennis_worker.pipeline.pose.serialization import from_artifact, to_artifact
from tennis_worker.pipeline.types import LANDMARK_COUNT, PoseSequence
from tests.synthetic import make_pose


def points(value: float) -> list[list[float]]:
    return [[value, value, value]] * LANDMARK_COUNT


def test_builder_marks_missing_frames() -> None:
    builder = PoseSequenceBuilder(fps=30)
    builder.add_detection(points(0.0), points(0.0), [1.0] * LANDMARK_COUNT)
    builder.add_missing()
    builder.add_detection(points(2.0), points(2.0), [1.0] * LANDMARK_COUNT)
    pose = builder.build()
    assert pose.frame_count == 3
    assert pose.detected.tolist() == [True, False, True]
    assert np.isnan(pose.world[1]).all()
    assert pose.detection_rate == pytest.approx(2 / 3)


def test_empty_builder_produces_empty_sequence() -> None:
    pose = PoseSequenceBuilder(fps=30).build()
    assert pose.frame_count == 0 and pose.detection_rate == 0.0


def test_interpolate_gaps_fills_linearly_without_adding_confidence() -> None:
    builder = PoseSequenceBuilder(fps=30)
    builder.add_detection(points(0.0), points(0.0), [1.0] * LANDMARK_COUNT)
    builder.add_missing()
    builder.add_detection(points(2.0), points(2.0), [1.0] * LANDMARK_COUNT)
    filled = interpolate_gaps(builder.build())
    np.testing.assert_allclose(filled.world[1], 1.0)
    assert filled.visibility[1].sum() == 0 and not filled.detected[1]


def test_interpolate_gaps_is_noop_when_nothing_to_fill() -> None:
    pose = make_pose()
    assert interpolate_gaps(pose) is pose


def test_pose_sequence_validates_shapes() -> None:
    pose = make_pose()
    with pytest.raises(ValueError, match="world"):
        replace(pose, world=pose.world[:, :10])
    with pytest.raises(ValueError, match="fps"):
        replace(pose, fps=0)


def test_artifact_follows_contract() -> None:
    builder = PoseSequenceBuilder(fps=30)
    builder.add_missing()
    builder.add_detection(points(0.5), points(0.25), [0.9] * LANDMARK_COUNT)
    artifact = to_artifact(builder.build())
    assert artifact["schema_version"] == "1.0" and artifact["landmark_count"] == LANDMARK_COUNT
    first, second = artifact["frames"]
    assert first["detected"] is False and first["world"][0] == [0.0, 0.0, 0.0]  # NaN → 0
    assert second["world"][0] == [0.25, 0.25, 0.25] and len(second["visibility"]) == LANDMARK_COUNT


def test_artifact_validates_against_json_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    import json

    from tests.conftest import CONTRACTS_DIR

    schema = json.loads((CONTRACTS_DIR / "pose_sequence.schema.json").read_text(encoding="utf-8"))
    pose: PoseSequence = make_pose()
    jsonschema.validate(to_artifact(pose), schema)


def test_artifact_round_trip_preserves_pose() -> None:
    pose = make_pose()
    restored = from_artifact(to_artifact(pose))
    assert restored.fps == pose.fps and restored.frame_count == pose.frame_count
    np.testing.assert_allclose(restored.world[pose.detected], pose.world[pose.detected], atol=1e-4)
    assert (restored.detected == pose.detected).all()
