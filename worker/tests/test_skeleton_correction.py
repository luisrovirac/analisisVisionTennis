from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from tennis_worker.pipeline.landmarks import MIRROR_ORDER, RIGHT
from tennis_worker.pipeline.orchestrator import prepare_pose
from tennis_worker.pipeline.pose.skeleton_correction import SkeletonCorrector, segments
from tennis_worker.pipeline.types import PoseSequence
from tests.synthetic import make_pose

corrector = SkeletonCorrector()


def flipped(pose: PoseSequence, frames: slice) -> PoseSequence:
    """Lo que hace BlazePose cuando duda: el cuerpo entero cambia de lado, con la visibilidad intacta."""
    image, world, visibility = pose.image.copy(), pose.world.copy(), pose.visibility.copy()
    for array in (image, world, visibility):
        array[frames] = array[frames][:, MIRROR_ORDER]
    return replace(pose, image=image, world=world, visibility=visibility)


def test_clean_swing_is_left_untouched() -> None:
    pose = make_pose()
    correction = corrector.correct(pose)
    assert not correction.changed
    assert correction.pose is pose


def test_mirror_order_is_an_involution_that_swaps_sides() -> None:
    assert (MIRROR_ORDER[MIRROR_ORDER] == np.arange(len(MIRROR_ORDER))).all()
    assert MIRROR_ORDER[RIGHT.wrist] == 15 and MIRROR_ORDER[0] == 0  # la nariz no tiene pareja


@pytest.mark.parametrize("frames", [slice(60, 76), slice(68, 69), slice(0, 10), slice(130, 140)])
def test_whole_body_flip_is_undone(frames: slice) -> None:
    pose = make_pose()
    correction = corrector.correct(flipped(pose, frames))
    expected = np.zeros(pose.frame_count, dtype=bool)
    expected[frames] = True
    np.testing.assert_array_equal(correction.flipped, expected)
    np.testing.assert_allclose(correction.pose.image, pose.image)
    np.testing.assert_allclose(correction.pose.world, pose.world)
    np.testing.assert_allclose(correction.pose.visibility, pose.visibility)


def test_mostly_wrong_stretch_stays_consistent_but_wrong() -> None:
    """Limitación documentada: sin ancla (raqueta) la continuidad no sabe qué orientación es la buena."""
    pose = make_pose()
    correction = corrector.correct(flipped(pose, slice(0, 120)))
    assert correction.flipped[120:].all() and not correction.flipped[:120].any()


def test_single_frame_spike_is_replaced_by_the_midpoint_of_its_neighbours() -> None:
    pose = make_pose()
    image, world = pose.image.copy(), pose.world.copy()
    image[30, RIGHT.wrist, :2] += [0.5, 0.3]  # ~1,2 torsos
    world[30, RIGHT.wrist, :2] += [0.5, 0.3]
    correction = corrector.correct(replace(pose, image=image, world=world))
    assert correction.spikes.sum() == 1 and correction.spikes[30, RIGHT.wrist]
    expected = (pose.world[29, RIGHT.wrist] + pose.world[31, RIGHT.wrist]) / 2
    np.testing.assert_allclose(correction.pose.world[30, RIGHT.wrist], expected)
    np.testing.assert_allclose(correction.pose.image[29], pose.image[29])  # los vecinos no se tocan
    np.testing.assert_allclose(correction.pose.image[31], pose.image[31])


def test_missing_frames_split_the_chain_and_are_not_filled() -> None:
    pose = flipped(make_pose(), slice(80, 140))
    detected = pose.detected.copy()
    detected[75:80] = False
    image, world = pose.image.copy(), pose.world.copy()
    image[75:80], world[75:80] = np.nan, np.nan
    correction = corrector.correct(replace(pose, image=image, world=world, detected=detected))
    # Tras el hueco, el tramo 80-139 es entero volteado y continuo: sin ancla no hay evidencia para cambiarlo.
    assert not correction.flipped.any()
    assert np.isnan(correction.pose.world[75:80]).all()


def test_person_jump_splits_the_chain() -> None:
    pose = make_pose()
    image = pose.image.copy()
    image[70:, :, 0] += 2.0  # 4 torsos: otra persona en otro sitio
    correction = corrector.correct(replace(pose, image=image))
    assert corrector.person_jumps(image[..., :2], np.full(pose.frame_count, 0.5))[70]
    assert not correction.spikes[69:72].any()


def test_segments() -> None:
    valid = np.array([True, True, False, True, True, True, False])
    jumps = np.array([False, False, False, False, True, False, False])
    assert segments(valid, jumps) == [(0, 2), (3, 4), (4, 6)]
    assert segments(np.ones(3, dtype=bool), np.zeros(3, dtype=bool)) == [(0, 3)]


def test_frame_size_converts_to_pixels_before_measuring() -> None:
    pose = replace(make_pose(), frame_size=(1920, 1080))
    correction = corrector.correct(flipped(pose, slice(60, 76)))
    assert correction.flipped[60:76].all() and correction.flipped.sum() == 16


def test_prepare_pose_corrects_before_interpolating_and_smoothing() -> None:
    clean = make_pose()
    raw = flipped(clean, slice(60, 76))
    np.testing.assert_allclose(prepare_pose(raw, corrector, 0.1).world, prepare_pose(clean, None, 0.1).world)
    assert not np.allclose(prepare_pose(raw, None, 0.1).world, prepare_pose(clean, None, 0.1).world)


def test_tiny_sequences() -> None:
    pose = make_pose()
    for frames in (0, 1, 2):
        short = PoseSequence(fps=pose.fps, image=pose.image[:frames], world=pose.world[:frames],
                             visibility=pose.visibility[:frames], detected=pose.detected[:frames])
        assert not corrector.correct(short).changed
