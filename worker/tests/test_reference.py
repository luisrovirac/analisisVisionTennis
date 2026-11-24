from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import numpy as np
import pytest

from tennis_worker.contracts import CameraView, StrokeType
from tennis_worker.infrastructure.reference_store import S3ReferenceStore
from tennis_worker.pipeline.landmarks import LEFT, RIGHT
from tennis_worker.pipeline.reference import ReferenceComparator, ReferenceStroke, dtw
from tennis_worker.pipeline.reference.features import frame_features
from tennis_worker.pipeline.reference.serialization import (
    INDEX_KEY,
    from_reference_artifact,
    index_entry,
    reference_key,
    to_reference_artifact,
    upsert_index,
)
from tennis_worker.pipeline.segmentation import WristSpeedSegmenter
from tests.conftest import CONTRACTS_DIR
from tests.synthetic import BAD_SWING, GOOD_SWING, SwingSpec, make_pose

# ---------- DTW ----------


def test_identical_sequences_cost_nothing() -> None:
    a = np.sin(np.linspace(0, 3, 40))[:, None]
    result = dtw(a, a)
    assert result.cost == pytest.approx(0.0)
    assert result.path[0] == (0, 0) and result.path[-1] == (39, 39)


def test_time_stretching_is_absorbed() -> None:
    base = np.sin(np.linspace(0, 3, 30))[:, None]
    stretched = np.sin(np.linspace(0, 3, 45))[:, None]  # el mismo gesto, un 50 % más lento
    different = np.cos(np.linspace(0, 3, 30))[:, None]
    assert dtw(base, stretched).cost < 0.05
    assert dtw(base, different).cost > 5 * dtw(base, stretched).cost


def test_band_never_excludes_the_diagonal() -> None:
    result = dtw(np.zeros((3, 2)), np.zeros((20, 2)), band=0.0)
    assert result.cost == pytest.approx(0.0)


def test_empty_sequence_is_rejected() -> None:
    with pytest.raises(ValueError):
        dtw(np.zeros((0, 1)), np.zeros((4, 1)))


# ---------- Características ----------


def mirror(spec_pose: Any) -> Any:
    """El mismo golpe hecho por un zurdo: espejo en x e intercambio de lados."""
    world = spec_pose.world.copy()
    world[..., 0] *= -1
    for a, b in zip(LEFT.__dict__.values(), RIGHT.__dict__.values(), strict=True):
        world[:, [a, b]] = world[:, [b, a]]
    return replace(spec_pose, world=world, image=world.copy())


def test_left_handed_stroke_matches_its_right_handed_mirror() -> None:
    pose = make_pose(GOOD_SWING)
    impact = WristSpeedSegmenter().segment(pose, "right").impact_frame
    right = frame_features(pose, "right", impact)
    left = frame_features(mirror(pose), "left", impact)
    np.testing.assert_allclose(left, right, atol=1e-6)


# ---------- Comparador ----------


class FakeProvider:
    def __init__(self, refs: list[ReferenceStroke]) -> None:
        self.refs = refs

    def references(self, stroke: StrokeType, view: CameraView) -> list[ReferenceStroke]:
        return [r for r in self.refs if r.stroke == stroke and r.camera_view == view]


def reference(spec: SwingSpec, ref_id: str = "pro-1") -> ReferenceStroke:
    pose = make_pose(spec)
    return ReferenceStroke(ref_id=ref_id, label=f"Modelo {ref_id}", stroke="forehand", camera_view="side",
                           handedness="right", source="test", pose=pose,
                           segment=WristSpeedSegmenter().segment(pose, "right"))


def compare(spec: SwingSpec, refs: list[ReferenceStroke], handedness: str = "right", pose: Any = None):  # type: ignore[no-untyped-def]
    pose = pose if pose is not None else make_pose(spec)
    segment = WristSpeedSegmenter().segment(pose, handedness)  # type: ignore[arg-type]
    return ReferenceComparator(FakeProvider(refs)).compare(pose, segment, handedness, "forehand", "side")  # type: ignore[arg-type]


def test_without_references_says_so() -> None:
    result = compare(GOOD_SWING, [])
    assert result.status == "no_references" and result.overall_similarity is None and not result.phases


def test_same_stroke_is_nearly_identical_and_a_bad_one_scores_lower() -> None:
    same = compare(GOOD_SWING, [reference(GOOD_SWING)])
    bad = compare(BAD_SWING, [reference(GOOD_SWING)])
    assert same.status == "ok" and same.overall_similarity is not None and same.overall_similarity > 99
    assert [p.phase for p in same.phases] == ["preparation", "acceleration", "follow_through"]
    assert bad.overall_similarity is not None and bad.overall_similarity < same.overall_similarity - 20
    assert same.references_used[0].ref_id == "pro-1"


def test_best_matching_reference_is_used() -> None:
    result = compare(GOOD_SWING, [reference(BAD_SWING, "lejano"), reference(GOOD_SWING, "cercano")])
    assert result.references_used[0].ref_id == "cercano"


def test_other_view_or_stroke_references_are_ignored() -> None:
    other = replace(reference(GOOD_SWING), camera_view="front")
    assert compare(GOOD_SWING, [other]).status == "no_references"


def test_left_handed_player_is_compared_with_right_handed_reference() -> None:
    pose = mirror(make_pose(GOOD_SWING))
    result = compare(GOOD_SWING, [reference(GOOD_SWING)], handedness="left", pose=pose)
    assert result.overall_similarity is not None and result.overall_similarity > 99


def test_low_visibility_abstains_instead_of_scoring() -> None:
    blurry = compare(SwingSpec(visibility=0.3), [reference(GOOD_SWING)])
    assert blurry.status == "low_confidence" and blurry.overall_similarity is None
    assert all(p.similarity is None for p in blurry.phases)
    assert {p.status for p in blurry.phases} <= {"low_confidence", "too_short"}


def test_partially_visible_stroke_scores_only_reliable_phases() -> None:
    pose = make_pose(GOOD_SWING)
    segment = WristSpeedSegmenter().segment(pose, "right")
    visibility = pose.visibility.copy()
    visibility[segment.impact_frame + 1:] = 0.1  # se pierde al jugador tras el impacto
    result = compare(GOOD_SWING, [reference(GOOD_SWING)], pose=replace(pose, visibility=visibility))
    statuses = {p.phase: p.status for p in result.phases}
    assert result.status == "partial" and statuses["follow_through"] == "low_confidence"
    assert statuses["acceleration"] == "ok"


# ---------- Serialización y almacén ----------


def test_reference_artifact_round_trip_and_schema() -> None:
    ref = reference(GOOD_SWING)
    artifact = to_reference_artifact(ref)
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((CONTRACTS_DIR / "reference_stroke.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(artifact, schema)
    restored = from_reference_artifact(json.loads(json.dumps(artifact)))
    assert restored.segment == ref.segment and restored.label == ref.label
    np.testing.assert_allclose(restored.pose.world, ref.pose.world, atol=1e-4)


def test_index_upsert_replaces_same_reference() -> None:
    ref = reference(GOOD_SWING)
    index = upsert_index(None, index_entry(ref, "t1"))
    index = upsert_index(index, index_entry(ref, "t2"))
    index = upsert_index(index, index_entry(replace(ref, ref_id="pro-2"), "t3"))
    assert [(r["ref_id"], r["registered_at"]) for r in index["references"]] == [("pro-1", "t2"), ("pro-2", "t3")]


class FakeJsonStore:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.reads: list[str] = []

    def get_json(self, key: str) -> dict[str, Any] | None:
        self.reads.append(key)
        return self.objects.get(key)


def test_store_filters_by_stroke_and_view_and_caches() -> None:
    store = FakeJsonStore()
    ref = reference(GOOD_SWING)
    store.objects[reference_key("forehand", "pro-1")] = to_reference_artifact(ref)
    store.objects[INDEX_KEY] = upsert_index(None, index_entry(ref, "t1"))
    now = [0.0]
    refs = S3ReferenceStore(store, refresh_seconds=60, clock=lambda: now[0])
    assert [r.ref_id for r in refs.references("forehand", "side")] == ["pro-1"]
    assert refs.references("forehand", "front") == [] and refs.references("serve", "side") == []
    assert store.reads.count(reference_key("forehand", "pro-1")) == 1  # descargada una sola vez
    assert store.reads.count(INDEX_KEY) == 1  # el índice no se relee antes de refresh_seconds
    now[0] = 61.0
    refs.references("forehand", "side")
    assert store.reads.count(INDEX_KEY) == 2


def test_store_without_index_has_no_references() -> None:
    assert S3ReferenceStore(FakeJsonStore()).references("forehand", "side") == []


def test_all_three_phases_are_scored_when_the_backswing_is_visible() -> None:
    pose = make_pose(GOOD_SWING)
    segment = WristSpeedSegmenter().segment(pose, "right").model_copy(update={"backswing_frame": 40})
    ref = replace(reference(GOOD_SWING), segment=segment)
    result = ReferenceComparator(FakeProvider([ref])).compare(pose, segment, "right", "forehand", "side")
    assert result.status == "ok" and all(p.status == "ok" for p in result.phases)
    assert result.overall_similarity is not None and result.overall_similarity > 99


# ---------- Criterios de aceptación de una referencia ----------


def _pose_lost_outside_stroke() -> tuple[Any, Any]:
    """Golpe perfecto, pero el jugador no se detecta en los fotogramas fuera del segmento (clip corto)."""
    from tennis_worker.pipeline.reference.acceptance import detection_rate_in

    pose = make_pose(GOOD_SWING)
    segment = WristSpeedSegmenter().segment(pose, "right")
    detected = pose.detected.copy()
    detected[:segment.start_frame] = False
    detected[segment.end_frame + 1:] = False
    lost = replace(pose, detected=detected)
    assert detection_rate_in(lost, slice(0, lost.frame_count)) < 0.9  # el escenario tiene sentido
    return lost, segment


def test_stroke_scope_ignores_frames_outside_the_stroke() -> None:
    from tennis_worker.pipeline.reference.acceptance import AcceptanceCriteria, assess_reference

    pose, segment = _pose_lost_outside_stroke()
    stroke = assess_reference(pose, segment)  # por defecto: ámbito "stroke"
    clip = assess_reference(pose, segment, AcceptanceCriteria(detection_scope="clip"))
    assert stroke.accepted and stroke.detection_rate == pytest.approx(1.0)
    assert not clip.accepted and "clip" in clip.problems[0]


def test_stroke_scope_still_rejects_missing_detections_inside_the_stroke() -> None:
    from tennis_worker.pipeline.reference.acceptance import assess_reference

    pose = make_pose(GOOD_SWING)
    segment = WristSpeedSegmenter().segment(pose, "right")
    detected = pose.detected.copy()
    detected[segment.start_frame:segment.impact_frame] = False
    result = assess_reference(replace(pose, detected=detected), segment)
    assert not result.accepted and result.detection_rate < 0.9


def test_low_phase_visibility_rejects_the_reference() -> None:
    from tennis_worker.pipeline.reference.acceptance import assess_reference

    pose = make_pose(SwingSpec(visibility=0.3))
    result = assess_reference(pose, WristSpeedSegmenter().segment(pose, "right"))
    assert not result.accepted and any("visibilidad" in p for p in result.problems)


def test_new_detection_scopes_can_be_registered() -> None:
    from tennis_worker.pipeline.reference import acceptance

    pose, segment = _pose_lost_outside_stroke()
    acceptance.DETECTION_SCOPES["impact_only"] = lambda p, s: slice(s.impact_frame, s.impact_frame + 1)
    try:
        criteria = acceptance.AcceptanceCriteria(detection_scope="impact_only")
        result = acceptance.assess_reference(pose, segment, criteria)
        assert result.accepted and result.detection_frames == slice(segment.impact_frame, segment.impact_frame + 1)
    finally:
        del acceptance.DETECTION_SCOPES["impact_only"]


def test_invalid_criteria_are_rejected() -> None:
    from tennis_worker.pipeline.reference.acceptance import AcceptanceCriteria

    with pytest.raises(ValueError):
        AcceptanceCriteria(detection_scope="nope")
    with pytest.raises(ValueError):
        AcceptanceCriteria(min_detection_rate=1.5)


# ---------- Referencias con fotogramas sin detección (regresión) ----------


def test_reference_with_undetected_frames_survives_the_artifact_round_trip() -> None:
    """Fotogramas sin jugador antes del golpe y un hueco dentro: from_artifact los devuelve como NaN y
    np.unwrap los propagaba a todo el giro de hombros/caderas ("la banda no permite ningún camino")."""
    from tennis_worker.pipeline.pose.postprocess import interpolate_gaps

    pose = make_pose(GOOD_SWING)
    segment = WristSpeedSegmenter().segment(pose, "right")
    detected = pose.detected.copy()
    detected[:3] = False
    detected[segment.impact_frame - 2] = False
    world = pose.world.copy()
    world[~detected] = np.nan
    lost = interpolate_gaps(replace(pose, detected=detected, world=world))
    ref = ReferenceStroke(ref_id="huecos", label="Huecos", stroke="forehand", camera_view="side",
                          handedness="right", source="test", pose=lost, segment=segment)
    loaded = from_reference_artifact(json.loads(json.dumps(to_reference_artifact(ref))))
    assert np.isfinite(loaded.pose.world).all()
    result = compare(GOOD_SWING, [loaded])
    assert result.status == "ok" and result.overall_similarity is not None and result.overall_similarity > 95


def test_dtw_rejects_nan_with_a_clear_message() -> None:
    with pytest.raises(ValueError, match="NaN"):
        dtw(np.array([[0.0], [np.nan]]), np.zeros((3, 1)))
