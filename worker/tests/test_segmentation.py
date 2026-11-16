from __future__ import annotations

import pytest

from tennis_worker.errors import NoStrokeFoundError
from tennis_worker.pipeline.segmentation import WristSpeedSegmenter
from tests.synthetic import GOOD_SWING, make_pose, static_pose


def test_finds_impact_at_wrist_speed_peak() -> None:
    segment = WristSpeedSegmenter().segment(make_pose(), "right")
    assert abs(segment.impact_frame - GOOD_SWING.impact) <= 1
    assert segment.start_frame == segment.impact_frame - 60
    assert segment.end_frame == segment.impact_frame + 60
    assert segment.start_frame <= segment.backswing_frame < segment.impact_frame - 20


def test_window_is_clipped_to_video_bounds() -> None:
    segment = WristSpeedSegmenter(window_seconds=5).segment(make_pose(), "right")
    assert segment.start_frame == 0
    assert segment.end_frame == GOOD_SWING.frames - 1


def test_static_player_has_no_stroke() -> None:
    with pytest.raises(NoStrokeFoundError):
        WristSpeedSegmenter().segment(static_pose(), "right")
