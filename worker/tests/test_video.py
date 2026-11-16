from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from tennis_worker.errors import VideoTooLongError, VideoUnreadableError
from tennis_worker.pipeline.video import OpenCVVideoReader


def write_video(path: Path, frames: int, fps: float = 10, size: tuple[int, int] = (320, 960)) -> Path:
    width, height = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (width, height))
    for i in range(frames):
        frame = np.full((height, width, 3), (i * 10) % 255, dtype=np.uint8)
        frame[..., 2] = 200  # canal rojo en BGR
        writer.write(frame)
    writer.release()
    return path


def test_reads_downscaled_rgb_frames(tmp_path: Path) -> None:
    video = write_video(tmp_path / "v.avi", frames=5)
    with OpenCVVideoReader(max_seconds=10, max_height=480).open(video) as stream:
        frames = list(stream.frames())
        assert stream.fps == pytest.approx(10)
    assert len(frames) == 5
    assert frames[0].shape == (480, 160, 3)
    assert frames[0][..., 0].mean() > 150  # el rojo queda en el canal 0 (RGB)


def test_rejects_too_long_video(tmp_path: Path) -> None:
    video = write_video(tmp_path / "v.avi", frames=25)
    with OpenCVVideoReader(max_seconds=2).open(video) as stream, pytest.raises(VideoTooLongError):
        list(stream.frames())


def test_rejects_unreadable_file(tmp_path: Path) -> None:
    bogus = tmp_path / "v.mp4"
    bogus.write_bytes(b"no es un video")
    with pytest.raises(VideoUnreadableError), OpenCVVideoReader(max_seconds=10).open(bogus):
        pass
