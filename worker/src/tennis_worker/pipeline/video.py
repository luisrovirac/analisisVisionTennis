"""Lectura y normalización de video. Los fotogramas se procesan en streaming para no cargar el video en memoria."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from tennis_worker.errors import VideoTooLongError, VideoUnreadableError

Frame = NDArray[np.uint8]  # (H, W, 3) RGB


class VideoStream(Protocol):
    @property
    def fps(self) -> float: ...

    def frames(self) -> Iterator[Frame]: ...


class VideoReader(Protocol):
    def open(self, path: Path) -> AbstractContextManager[VideoStream]: ...


@dataclass(frozen=True)
class _OpenCVStream:
    capture: Any
    fps: float
    max_frames: int
    max_height: int

    def frames(self) -> Iterator[Frame]:
        for index in range(self.max_frames + 1):
            ok, bgr = self.capture.read()
            if not ok:
                return
            if index == self.max_frames:
                raise VideoTooLongError(f"más de {self.max_frames} fotogramas")
            yield self._normalize(bgr)

    def _normalize(self, bgr: Frame) -> Frame:
        height, width = bgr.shape[:2]
        if height > self.max_height:
            scale = self.max_height / height
            size = (round(width * scale), self.max_height)
            bgr = np.asarray(cv2.resize(bgr, size, interpolation=cv2.INTER_AREA), dtype=np.uint8)
        return np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


@dataclass(frozen=True)
class OpenCVVideoReader:
    max_seconds: float
    max_height: int = 720

    @contextmanager
    def open(self, path: Path) -> Iterator[VideoStream]:
        capture = cv2.VideoCapture(str(path))
        try:
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            if not capture.isOpened() or fps <= 0:
                raise VideoUnreadableError(f"OpenCV no pudo abrir {path.name}")
            yield _OpenCVStream(capture, fps, int(self.max_seconds * fps), self.max_height)
        finally:
            capture.release()
