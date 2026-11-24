"""Compara estimadores de pose (MediaPipe vs RTMPose + MotionBERT) sobre los mismos videos.

Cada video pasa por el pipeline real del worker con cada estimador (relleno de huecos, suavizado, segmentación,
impacto por audio, métricas con prueba de sensibilidad) y se imprime, por métrica, el valor y la confianza.
La confianza ya incluye la estabilidad ante el fotograma de impacto: un estimador mejor da más métricas
fiables (confianza >= 0,5) y valores que no saltan.

Uso (desde la raíz del repo, con el entorno del worker):

    worker/.venv/Scripts/python.exe scripts/compare_pose.py samples/*.mp4 --stroke forehand \
        --motionbert ml/export/motionbert/model.onnx

Los modelos de RTMPose se descargan (verificados) a --cache la primera vez. Para decidir de verdad, usa el
conjunto de referencia del protocolo de grabación (Docs/BASE_CONOCIMIENTO.md), no un único video.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker" / "src"))

from tennis_worker.contracts import MetricValue  # noqa: E402
from tennis_worker.pipeline.audio import AudioImpactRefiner, PyAvAudioReader  # noqa: E402
from tennis_worker.pipeline.biomechanics.base import MetricContext  # noqa: E402
from tennis_worker.pipeline.biomechanics.registry import default_registry  # noqa: E402
from tennis_worker.pipeline.biomechanics.robustness import TemporalRobustness  # noqa: E402
from tennis_worker.pipeline.orchestrator import SizeRecordingStream, prepare_pose  # noqa: E402
from tennis_worker.pipeline.pose import PoseEstimator  # noqa: E402
from tennis_worker.pipeline.pose.skeleton_correction import SkeletonCorrector  # noqa: E402
from tennis_worker.pipeline.segmentation import PeakStrokeSegmenter  # noqa: E402
from tennis_worker.pipeline.video import OpenCVVideoReader  # noqa: E402

MIN_CONFIDENCE = 0.5


@dataclass
class Run:
    estimator: str
    seconds: float
    detection_rate: float
    impact_source: str
    metrics: dict[str, MetricValue]
    stability: dict[str, float]  # confianza robusta / confianza nominal: no depende de la escala del estimador

    @property
    def reliable(self) -> int:
        return sum(m.confidence >= MIN_CONFIDENCE for m in self.metrics.values())

    @property
    def stable(self) -> int:
        return sum(s >= MIN_CONFIDENCE for s in self.stability.values())

    @property
    def mean_confidence(self) -> float:
        return sum(m.confidence for m in self.metrics.values()) / max(1, len(self.metrics))


def build_estimators(args: argparse.Namespace) -> dict[str, PoseEstimator]:
    from tennis_worker.infrastructure.pose_models import RTMPOSE_M_BODY7, YOLOX_M_HUMANART, ensure_onnx
    from tennis_worker.pipeline.pose.lifting import MotionBertLifter
    from tennis_worker.pipeline.pose.mediapipe_estimator import MediaPipePoseEstimator
    from tennis_worker.pipeline.pose.rtm_motionbert_estimator import RtmPoseMotionBertEstimator
    from tennis_worker.pipeline.pose.rtmpose import RtmPoseModel, YoloxPersonDetector, onnx_session

    estimators: dict[str, PoseEstimator] = {"mediapipe": MediaPipePoseEstimator(args.mediapipe_model)}
    if args.motionbert:
        estimators["rtmpose_motionbert"] = RtmPoseMotionBertEstimator(
            detector=YoloxPersonDetector(onnx_session(ensure_onnx(YOLOX_M_HUMANART, args.cache))),
            pose_model=RtmPoseModel(onnx_session(ensure_onnx(RTMPOSE_M_BODY7, args.cache))),
            lifter=MotionBertLifter(onnx_session(args.motionbert)),
        )
    return estimators


def analyse(name: str, estimator: PoseEstimator, video: Path, args: argparse.Namespace) -> Run:
    started = time.perf_counter()
    with OpenCVVideoReader(max_seconds=60).open(video) as stream:
        sized = SizeRecordingStream(stream)
        raw = replace(estimator.estimate(sized), frame_size=sized.size)
    seconds = time.perf_counter() - started
    pose = prepare_pose(raw, None if args.no_skeleton_correction else SkeletonCorrector(), args.smoothing)
    segmenter = PeakStrokeSegmenter()
    segment = segmenter.segment(pose, args.handedness, args.stroke)
    refined = AudioImpactRefiner(PyAvAudioReader(), rebuild=segmenter.segment_at).refine(
        segment, pose, video, args.handedness)
    uncertainty = 1.0 / pose.fps if refined.source == "audio" else 0.0
    ctx = MetricContext(pose, refined.segment, args.handedness, impact_uncertainty_s=uncertainty)
    nominal = default_registry().compute_all(args.stroke, ctx)
    metrics = TemporalRobustness(default_registry()).compute_all(args.stroke, ctx)
    stability = {k: (metrics[k].confidence / v.confidence if v.confidence > 0 else 0.0) for k, v in nominal.items()}
    return Run(name, seconds, pose.detection_rate, refined.source, metrics, stability)


def print_comparison(video: Path, runs: list[Run]) -> None:
    print(f"\n=== {video.name} ===")
    print(f"{'':32s}" + "".join(f"{r.estimator:>26s}" for r in runs))
    for label, value in (("tiempo de pose (s)", lambda r: f"{r.seconds:.1f}"),
                         ("detección", lambda r: f"{r.detection_rate:.0%}"),
                         ("impacto", lambda r: r.impact_source),
                         (f"métricas fiables (>= {MIN_CONFIDENCE})", lambda r: f"{r.reliable}/{len(r.metrics)}"),
                         ("métricas estables (solo tiempo)", lambda r: f"{r.stable}/{len(r.metrics)}"),
                         ("confianza media", lambda r: f"{r.mean_confidence:.2f}")):
        print(f"{label:32s}" + "".join(f"{value(r):>26s}" for r in runs))
    print("-" * (32 + 26 * len(runs)) + "\n(confianza | estabilidad ante el fotograma de impacto)")
    for metric_id in runs[0].metrics:
        cells = []
        for run in runs:
            m = run.metrics[metric_id]
            mark = "" if m.confidence >= MIN_CONFIDENCE else " ✗"
            cells.append(f"{m.value:9.3f} {m.unit:5s} ({m.confidence:.2f}|{run.stability[metric_id]:.2f}){mark}")
        print(f"{metric_id:32s}" + "".join(f"{c:>26s}" for c in cells))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("videos", type=Path, nargs="+")
    parser.add_argument("--stroke", default="forehand", choices=["forehand", "backhand", "serve", "volley"])
    parser.add_argument("--handedness", default="right", choices=["right", "left"])
    parser.add_argument("--mediapipe-model", type=Path, default=ROOT / "models" / "pose_landmarker_full.task")
    parser.add_argument("--motionbert", type=Path, default=None, help="model.onnx de ml/export/motionbert")
    parser.add_argument("--cache", type=Path, default=ROOT / "models" / "cache")
    parser.add_argument("--smoothing", type=float, default=0.1)
    parser.add_argument("--no-skeleton-correction", action="store_true",
                        help="no deshacer inversiones izquierda/derecha ni picos")
    parser.add_argument("--json", type=Path, default=None, help="guarda los resultados en JSON")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # consolas de Windows
    estimators = build_estimators(args)
    results: dict[str, Any] = {}
    for video in args.videos:
        runs = [analyse(name, est, video, args) for name, est in estimators.items()]
        print_comparison(video, runs)
        results[video.name] = {r.estimator: {"seconds": r.seconds, "detection_rate": r.detection_rate,
                                             "impact_source": r.impact_source, "reliable": r.reliable,
                                             "stable": r.stable, "stability": r.stability,
                                             "metrics": {k: v.model_dump() for k, v in r.metrics.items()}}
                               for r in runs}
    if args.json:
        args.json.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
