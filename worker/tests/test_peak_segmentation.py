from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from tennis_worker.errors import NoStrokeFoundError
from tennis_worker.pipeline.landmarks import RIGHT, Landmark
from tennis_worker.pipeline.segmentation import (
    PeakStrokeSegmenter,
    WristSpeedSegmenter,
    find_peaks,
    merge_runs,
    peak_prominence,
    runs,
)
from tennis_worker.pipeline.types import PoseSequence
from tests.synthetic import GOOD_SWING, make_pose, static_pose

IMAGE_TOLERANCE = 3  # la señal por defecto (imagen) adelanta el pico del swing sintético 2 fotogramas
RECOVERY = 90  # fotogramas de vuelta lenta a la posición inicial entre golpes


def rally(strokes: int) -> tuple[PoseSequence, list[int]]:
    """Varios swings seguidos, unidos por una recuperación lenta (no es un golpe), y los impactos esperados."""
    swing = make_pose()
    ease = (1 - np.cos(np.linspace(0, np.pi, RECOVERY + 2)[1:-1])) / 2  # arranca y frena suave
    back = swing.world[-1] + (swing.world[0] - swing.world[-1]) * ease[:, None, None]
    pieces, impacts, offset = [], [], 0
    for i in range(strokes):
        pieces.append(swing.world)
        impacts.append(offset + GOOD_SWING.impact)
        offset += swing.frame_count
        if i < strokes - 1:
            pieces.append(back)
            offset += RECOVERY
    world = np.concatenate(pieces)
    frames = len(world)
    return PoseSequence(fps=swing.fps, image=world.copy(), world=world,
                        visibility=np.full((frames, world.shape[1]), 0.9),
                        detected=np.ones(frames, dtype=bool)), impacts


# En la imagen solo se ve la proyección x-y del arco (que gira en x-z): su pico de velocidad se adelanta.
@pytest.mark.parametrize(("signal", "tolerance"), [("world", 1), ("image", IMAGE_TOLERANCE)])
def test_finds_every_stroke_of_a_rally(signal: str, tolerance: int) -> None:
    pose, expected = rally(3)
    found = PeakStrokeSegmenter(signal=signal).candidates(pose, "right")  # type: ignore[arg-type]
    assert [c.kind for c in found] == ["groundstroke"] * 3
    assert all(abs(c.impact - e) <= tolerance for c, e in zip(found, expected, strict=True))


def test_segments_one_window_per_stroke() -> None:
    pose, expected = rally(2)
    segments = PeakStrokeSegmenter().segments(pose, "right", "forehand")
    assert [abs(s.impact_frame - e) <= IMAGE_TOLERANCE for s, e in zip(segments, expected, strict=True)] == [True, True]
    assert all(s.start_frame <= s.backswing_frame < s.impact_frame for s in segments)


def test_single_swing_matches_the_previous_segmenter() -> None:
    pose = make_pose()
    world = PeakStrokeSegmenter(signal="world").segment(pose, "right", "forehand")
    assert world == WristSpeedSegmenter().segment(pose, "right")
    image = PeakStrokeSegmenter().segment(pose, "right", "forehand")  # por defecto: imagen
    assert abs(image.impact_frame - GOOD_SWING.impact) <= 3


def test_static_player_has_no_stroke() -> None:
    assert PeakStrokeSegmenter().candidates(static_pose(), "right") == []
    with pytest.raises(NoStrokeFoundError):
        PeakStrokeSegmenter().segment(static_pose(), "right")


def test_falls_back_to_the_fastest_frame_when_no_peak_stands_out() -> None:
    """Movimiento continuo y rápido (sin reposo): ningún pico destaca 3× sobre la mediana, pero hay golpe."""
    pose = static_pose(240)
    world = pose.world.copy()
    t = np.arange(240) / pose.fps
    world[:, RIGHT.wrist, 0] += 0.5 * np.sin(2 * np.pi * t) * (1 + 0.1 * t)  # el último ciclo, algo más rápido
    pose = replace(pose, world=world, image=world.copy())
    segmenter = PeakStrokeSegmenter()
    assert segmenter.candidates(pose, "right", "forehand") == []
    speed = segmenter.wrist_speed(pose, "right")
    assert segmenter.segment(pose, "right", "forehand").impact_frame == int(np.argmax(speed))


def serve_pose() -> tuple[PoseSequence, int]:
    """Un swing (picos de velocidad) con la muñeca subiendo sobre la cabeza en otro momento: el saque."""
    pose = make_pose()
    world = pose.world.copy()
    top = 30
    bump = np.exp(-0.5 * ((np.arange(pose.frame_count) - top) / 6.0) ** 2)
    nose_y = world[:, Landmark.NOSE, 1]
    world[:, RIGHT.wrist, 1] = world[:, RIGHT.wrist, 1] * (1 - bump) + (nose_y - 0.7) * bump  # 1,4 torsos arriba
    return replace(pose, world=world, image=world.copy()), top


def test_serve_impact_is_the_highest_point_of_the_hitting_wrist() -> None:
    pose, top = serve_pose()
    found = PeakStrokeSegmenter().candidates(pose, "right", "serve")
    assert [c.kind for c in found] == ["serve"] and abs(found[0].impact - top) <= 1
    assert abs(PeakStrokeSegmenter().segment(pose, "right", "serve").impact_frame - top) <= 1


def test_groundstroke_ignores_the_serve_detector() -> None:
    pose, _ = serve_pose()
    found = PeakStrokeSegmenter().candidates(pose, "right", "forehand")
    assert {c.kind for c in found} == {"groundstroke"}


def test_serve_without_raised_wrist_falls_back_to_speed_peaks() -> None:
    found = PeakStrokeSegmenter().candidates(make_pose(), "right", "serve")
    assert [c.kind for c in found] == ["groundstroke"]


def test_left_handed_player_uses_the_left_wrist() -> None:
    pose, expected = rally(2)
    mirrored = replace(pose, world=pose.world[:, _mirror()], image=pose.image[:, _mirror()])
    found = PeakStrokeSegmenter().candidates(mirrored, "left", "forehand")
    assert [abs(c.impact - e) <= IMAGE_TOLERANCE for c, e in zip(found, expected, strict=True)] == [True, True]
    assert PeakStrokeSegmenter().candidates(mirrored, "right", "forehand") == []


def _mirror() -> np.ndarray:
    from tennis_worker.pipeline.landmarks import MIRROR_ORDER
    return MIRROR_ORDER


def test_find_peaks_uses_prominence_and_keeps_the_highest_within_distance() -> None:
    x = np.array([0, 5, 0, 0, 1, 0.5, 1.2, 0, 0, 4, 3.8, 4.1, 0, 0], dtype=float)
    # El 4 (índice 9) solo destaca 0,2 sobre su base derecha (3,8, antes del 4,1): no es un golpe aparte.
    assert find_peaks(x, prominence=2.0, distance=1) == [1, 11]
    assert find_peaks(x, prominence=0.6, distance=1) == [1, 6, 11]  # el 1 (índice 4) solo destaca 0,5
    assert find_peaks(x, prominence=0.1, distance=1) == [1, 4, 6, 9, 11]
    assert find_peaks(x, prominence=0.1, distance=3) == [1, 6, 11]  # cercanos: se quedan los más altos
    assert find_peaks(np.array([1.0, 2.0]), prominence=0, distance=1) == []


def test_peak_prominence() -> None:
    x = np.array([0, 3, 1, 5, 2, 4, 0], dtype=float)
    assert peak_prominence(x, 3) == 5  # el más alto: hasta los bordes
    assert peak_prominence(x, 1) == 2  # base izquierda 0, derecha 1 (hasta el 5) → max(0,1) = 1
    assert peak_prominence(x, 5) == 2  # base izquierda 2 (hasta el 5), derecha 0


def test_runs_and_merge() -> None:
    mask = np.array([0, 1, 1, 0, 0, 1, 0, 1, 1], dtype=bool)
    assert runs(mask) == [(1, 3), (5, 6), (7, 9)]
    assert merge_runs(runs(mask), gap=2) == [(1, 3), (5, 9)]
    assert merge_runs(runs(mask), gap=3) == [(1, 9)]
    assert runs(np.zeros(3, dtype=bool)) == []
