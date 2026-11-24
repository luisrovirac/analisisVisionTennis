"""Impacto por audio: el sonido de la bola contra las cuerdas fija el instante del impacto.

El segmentador estima el impacto por el pico de velocidad de la muñeca, que suele caer unos fotogramas antes
o después del contacto real. El golpe suena como un chasquido agudo y breve: se filtran los graves (pasos,
viento, voces), se calcula la envolvente y se busca el pico más fuerte cerca del pico de la muñeca. Solo se
acepta si sobresale claramente del ruido de fondo; si no, se conserva la estimación por la muñeca.

Idea y parámetros de partida: rate-my-tennis-skill y tennis-video-private-coach (MIT; ver
Docs/inspiracion02.md). Implementación propia.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np

from tennis_worker.contracts import Handedness, Segment
from tennis_worker.pipeline.types import FloatArray, PoseSequence

logger = logging.getLogger(__name__)

ImpactSource = Literal["audio", "wrist_speed"]


@dataclass(frozen=True)
class AudioTrack:
    samples: FloatArray  # mono, en [-1, 1]
    sample_rate: int
    start_seconds: float = 0.0  # desfase del audio respecto al primer fotograma de video


class AudioReader(Protocol):
    def read(self, path: Path) -> AudioTrack | None:
        """None si el video no tiene pista de audio o no se puede leer."""
        ...


@dataclass(frozen=True)
class PyAvAudioReader:
    """Lee la pista de audio con PyAV (FFmpeg incluido en la rueda: no hace falta instalar ffmpeg)."""

    sample_rate: int = 22050
    max_seconds: float = 60.0

    def read(self, path: Path) -> AudioTrack | None:
        import av  # import diferido: el resto del worker no depende de PyAV

        try:
            with av.open(str(path)) as container:
                if not container.streams.audio:
                    return None
                return self._decode(container)
        except (av.error.FFmpegError, OSError, ValueError):
            logger.warning("no se pudo leer el audio del video", exc_info=True)
            return None

    def _decode(self, container: Any) -> AudioTrack | None:
        import av

        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=self.sample_rate)
        chunks: list[FloatArray] = []
        limit = int(self.max_seconds * self.sample_rate)

        def collect(frame: Any) -> None:
            for out in resampler.resample(frame):
                chunks.append(np.asarray(out.to_ndarray(), dtype=float).reshape(-1))

        for frame in container.decode(stream):
            collect(frame)
            if sum(len(c) for c in chunks) >= limit:
                break
        collect(None)  # vacía el resampler
        if not chunks:
            return None
        return AudioTrack(np.concatenate(chunks)[:limit], self.sample_rate, _start_offset(container, stream))


def _start_offset(container: Any, audio_stream: Any) -> float:
    def start(stream: Any) -> float:
        value, base = getattr(stream, "start_time", None), getattr(stream, "time_base", None)
        return float(value * base) if value is not None and base is not None else 0.0

    videos = container.streams.video
    return start(audio_stream) - (start(videos[0]) if videos else 0.0)


def highpass(samples: FloatArray, sample_rate: int, cutoff_hz: float) -> FloatArray:
    """Filtro paso alto ideal por FFT (suficiente para clips cortos)."""
    spectrum = np.fft.rfft(samples)
    spectrum[np.fft.rfftfreq(len(samples), 1.0 / sample_rate) < cutoff_hz] = 0
    result: FloatArray = np.fft.irfft(spectrum, n=len(samples))
    return result


def envelope(samples: FloatArray, sample_rate: int, window_ms: float) -> FloatArray:
    width = max(1, round(window_ms / 1000 * sample_rate))
    result: FloatArray = np.convolve(np.abs(samples), np.ones(width) / width, mode="same")
    return result


@dataclass(frozen=True)
class AudioImpact:
    seconds: float  # en el tiempo del video
    prominence_db: float


@dataclass(frozen=True)
class AudioImpactDetector:
    highpass_hz: float = 1500.0
    envelope_ms: float = 5.0
    min_prominence_db: float = 10.0

    def detect(self, track: AudioTrack, around_seconds: float, before: float, after: float) -> AudioImpact | None:
        """Pico de golpe en [around - before, around + after] (segundos de video), o None si no destaca
        al menos `min_prominence_db` sobre la mediana de la envolvente del clip (el ruido de fondo)."""
        env = envelope(highpass(track.samples, track.sample_rate, self.highpass_hz), track.sample_rate,
                       self.envelope_ms)
        rate = track.sample_rate
        lo = max(0, round((around_seconds - before - track.start_seconds) * rate))
        hi = min(len(env), round((around_seconds + after - track.start_seconds) * rate))
        if hi - lo < 2:
            return None
        peak = lo + int(np.argmax(env[lo:hi]))
        noise = float(np.median(env)) + 1e-9
        prominence = 20 * np.log10(float(env[peak]) / noise + 1e-12)
        if prominence < self.min_prominence_db:
            return None
        return AudioImpact(seconds=peak / rate + track.start_seconds, prominence_db=round(float(prominence), 1))


@dataclass(frozen=True)
class RefinedSegment:
    segment: Segment
    source: ImpactSource
    note: str  # explicación para el jugador (va a las razones de fiabilidad del reporte)


@dataclass(frozen=True)
class AudioImpactRefiner:
    """Corrige el impacto del segmentador con el sonido del golpe. `rebuild` reconstruye el segmento
    (ventana y backswing) alrededor del nuevo fotograma de impacto."""

    reader: AudioReader
    rebuild: Callable[[PoseSequence, Handedness, int], Segment]
    detector: AudioImpactDetector = AudioImpactDetector()
    search_before_seconds: float = 0.25
    search_after_seconds: float = 0.35

    def refine(self, segment: Segment, pose: PoseSequence, video_path: Path,
               handedness: Handedness) -> RefinedSegment:
        track = self.reader.read(video_path)
        if track is None:
            return RefinedSegment(segment, "wrist_speed", "El video no tiene audio: el impacto se estimó por la "
                                                          "velocidad de la muñeca.")
        around = segment.impact_frame / segment.fps
        impact = self.detector.detect(track, around, self.search_before_seconds, self.search_after_seconds)
        if impact is None:
            return RefinedSegment(segment, "wrist_speed", "No se distinguió el sonido del golpe: el impacto se "
                                                          "estimó por la velocidad de la muñeca.")
        frame = int(np.clip(round(impact.seconds * segment.fps), 1, pose.frame_count - 1))
        logger.info("impacto por audio", extra={"wrist_frame": segment.impact_frame, "audio_frame": frame,
                                                "prominence_db": impact.prominence_db})
        return RefinedSegment(self.rebuild(pose, handedness, frame), "audio",
                              "Impacto confirmado por el sonido del golpe.")
