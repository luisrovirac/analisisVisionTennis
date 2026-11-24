"""Impacto por audio: detector sobre señales sintéticas y refinado del segmento."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from tennis_worker.contracts import Handedness
from tennis_worker.pipeline.audio import (
    AudioImpactDetector,
    AudioImpactRefiner,
    AudioTrack,
    PyAvAudioReader,
    highpass,
)
from tennis_worker.pipeline.segmentation import WristSpeedSegmenter
from tennis_worker.pipeline.types import PoseSequence
from tests.synthetic import make_pose

RATE = 22050
RNG = np.random.default_rng(7)


def track_with_click(seconds: float = 3.0, click_at: float | None = 1.3, click_gain: float = 0.8,
                     rumble: float = 0.3, start: float = 0.0) -> AudioTrack:
    """Ruido de fondo suave + un retumbo grave fuerte (pasos) + un chasquido agudo de 3 ms (el golpe)."""
    t = np.arange(int(seconds * RATE)) / RATE
    samples = 0.01 * RNG.standard_normal(len(t)) + rumble * np.sin(2 * np.pi * 80 * t)
    if click_at is not None:
        i = int((click_at - start) * RATE)
        n = int(0.003 * RATE)
        samples[i:i + n] += click_gain * np.sin(2 * np.pi * 4000 * np.arange(n) / RATE)
    return AudioTrack(samples, RATE, start)


def test_highpass_removes_low_frequencies() -> None:
    t = np.arange(RATE) / RATE
    low, high = np.sin(2 * np.pi * 80 * t), np.sin(2 * np.pi * 4000 * t)
    assert np.abs(highpass(low, RATE, 1500)).max() < 0.01
    assert np.abs(highpass(high, RATE, 1500)).max() > 0.9


def test_detects_the_click_and_ignores_the_low_rumble() -> None:
    impact = AudioImpactDetector().detect(track_with_click(click_at=1.3), around_seconds=1.2, before=0.25,
                                          after=0.35)
    assert impact is not None
    assert impact.seconds == pytest.approx(1.3, abs=0.005)
    assert impact.prominence_db > 20


def test_respects_the_audio_start_offset() -> None:
    impact = AudioImpactDetector().detect(track_with_click(click_at=1.3, start=0.5), 1.2, 0.25, 0.35)
    assert impact is not None and impact.seconds == pytest.approx(1.3, abs=0.005)


@pytest.mark.parametrize("track", [
    track_with_click(click_at=None),  # no hay golpe audible
    track_with_click(click_at=2.5),  # hay un chasquido, pero fuera de la ventana de búsqueda
])
def test_no_impact_without_a_prominent_click_in_the_window(track: AudioTrack) -> None:
    assert AudioImpactDetector().detect(track, 1.2, 0.25, 0.35) is None


@dataclass
class FakeReader:
    track: AudioTrack | None

    def read(self, path: Path) -> AudioTrack | None:
        return self.track


def refine(track: AudioTrack | None, pose: PoseSequence, handedness: Handedness = "right"):  # type: ignore[no-untyped-def]
    segmenter = WristSpeedSegmenter()
    segment = segmenter.segment(pose, handedness)
    refiner = AudioImpactRefiner(FakeReader(track), rebuild=segmenter.segment_at)
    return segment, refiner.refine(segment, pose, Path("video.mp4"), handedness)


def test_refiner_moves_the_impact_to_the_sound() -> None:
    pose = make_pose()  # 60 fps, pico de muñeca en el fotograma 70
    segment, refined = refine(track_with_click(click_at=73 / 60), pose)
    assert segment.impact_frame == 70
    assert refined.source == "audio" and refined.segment.impact_frame == 73
    assert refined.segment.start_frame <= refined.segment.backswing_frame < 73 <= refined.segment.end_frame
    assert "sonido" in refined.note


@pytest.mark.parametrize(("track", "phrase"), [(None, "no tiene audio"),
                                               (track_with_click(click_at=None), "No se distinguió")])
def test_refiner_keeps_the_wrist_estimate_when_audio_does_not_help(track: AudioTrack | None, phrase: str) -> None:
    segment, refined = refine(track, make_pose())
    assert refined.source == "wrist_speed" and refined.segment == segment and phrase in refined.note


def test_reader_returns_none_for_unreadable_files(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert PyAvAudioReader().read(broken) is None


def test_reader_decodes_a_generated_audio_file(tmp_path: Path) -> None:
    av = pytest.importorskip("av")
    path = tmp_path / "tone.wav"
    with av.open(str(path), "w") as container:
        stream = container.add_stream("pcm_s16le", rate=44100, layout="mono")
        samples = (np.sin(2 * np.pi * 440 * np.arange(44100) / 44100) * 10000).astype(np.int16)
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = 44100
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    track = PyAvAudioReader(sample_rate=22050).read(path)
    assert track is not None and track.sample_rate == 22050
    assert len(track.samples) / 22050 == pytest.approx(1.0, abs=0.05)
    assert np.abs(track.samples).max() == pytest.approx(10000 / 32768, abs=0.05)
