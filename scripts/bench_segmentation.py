#!/usr/bin/env python3
"""Banco de pruebas del segmentador contra impactos anotados a mano.

Anotaciones: references/golpes.csv de tennis-analyzer (https://github.com/rubenG1009/tennis-analyzer, MIT):
51 impactos en 11 clips de profesionales de YouTube, con su tolerancia en fotogramas. Los clips NO se
redistribuyen ni se usan como referencias del producto: solo para evaluar, en data/ (fuera de git). Se bajan
con el mismo formato y tramos que su download_references.py para que los números de fotograma coincidan.

    python scripts/bench_segmentation.py download   # yt-dlp (y ffmpeg) en el PATH
    python scripts/bench_segmentation.py extract    # pose cruda de cada clip (MediaPipe), una vez
    python scripts/bench_segmentation.py evaluate   # compara segmentadores; rápido, sin volver a la pose

Dos preguntas, como en su evaluar_segmentacion.py:
- ¿Encuentra los golpes? Una detección empareja con un impacto anotado si cae a menos de 0,5 s; con eso,
  exhaustividad (golpes encontrados) y precisión (detecciones que son golpes).
- ¿Acierta el impacto? Error en fotogramas de los emparejados frente a la tolerancia de la anotación.
Y una tercera para el producto actual (un golpe por informe): ¿el golpe que elige `segment()` es un golpe real?
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker" / "src"))

from tennis_worker.contracts import StrokeType  # noqa: E402
from tennis_worker.errors import NoStrokeFoundError  # noqa: E402
from tennis_worker.pipeline.orchestrator import SizeRecordingStream, prepare_pose  # noqa: E402
from tennis_worker.pipeline.pose.serialization import from_artifact, to_artifact  # noqa: E402
from tennis_worker.pipeline.pose.skeleton_correction import SkeletonCorrector  # noqa: E402
from tennis_worker.pipeline.segmentation import PeakStrokeSegmenter, WristSpeedSegmenter  # noqa: E402
from tennis_worker.pipeline.types import PoseSequence  # noqa: E402

UPSTREAM = ROOT.parent / "tennis-analyzer" / "references"
BENCH = ROOT / "data" / "bench" / "segmentation"
MATCH_SECONDS = 0.5
ANNOTATED_UNTIL = {"fh_compilacion_espalda": 1400}  # el resto del clip no está anotado
REAL_SPEED = {"rt_sinner_lat", "rt_alcaraz_lat"}  # verificados a velocidad real; el resto, cámara lenta editada
FORMAT = "bv*[vcodec^=avc1][height<=1080]/bv*[height<=1080]/b"


@dataclass(frozen=True)
class Annotation:
    clip: str
    impact: int
    tolerance: int
    stroke: str


def load_annotations(path: Path) -> dict[str, list[Annotation]]:
    result: dict[str, list[Annotation]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            result[row["clip"]].append(Annotation(row["clip"], int(row["impacto"]), int(row["tolerancia"]),
                                                  row["golpe"]))
    return dict(result)


def download(args: argparse.Namespace) -> None:
    download_clips(sorted(load_annotations(args.annotations)), args.annotations.parent / "manifest.csv", args.videos)


def download_clips(clips: list[str], manifest_path: Path, videos: Path) -> None:
    """Baja de YouTube los clips que falten, con el formato y los tramos de manifest.csv."""
    videos.mkdir(parents=True, exist_ok=True)
    with manifest_path.open(newline="", encoding="utf-8") as f:
        manifest = {row["nombre"]: row for row in csv.DictReader(f)}
    for clip in clips:
        target = videos / f"{clip}.mp4"
        if target.exists():
            continue
        row = manifest[clip]
        cmd = ["yt-dlp", "--no-warnings", "--no-playlist", "-f", FORMAT, "--remux-video", "mp4", "-o", str(target)]
        if row["inicio_s"] or row["fin_s"]:
            cmd += ["--download-sections", f"*{row['inicio_s'] or 0}-{row['fin_s'] or 'inf'}",
                    "--force-keyframes-at-cuts"]
        print(f"descargando {clip}…", flush=True)
        subprocess.run([*cmd, f"https://www.youtube.com/watch?v={row['youtube_id']}"], check=True)


def extract(args: argparse.Namespace) -> None:
    extract_poses({clip: args.videos / f"{clip}.mp4" for clip in sorted(load_annotations(args.annotations))},
                  args.poses, args.model)


def extract_poses(videos: dict[str, Path], poses: Path, model: Path) -> None:
    """Guarda la pose CRUDA (sin corregir, interpolar ni suavizar) de cada video que falte, para poder evaluar
    cada variante sin volver a pasar el modelo."""
    from tennis_worker.pipeline.pose.mediapipe_estimator import MediaPipePoseEstimator
    from tennis_worker.pipeline.video import OpenCVVideoReader

    estimator = MediaPipePoseEstimator(model)
    poses.mkdir(parents=True, exist_ok=True)
    for name, video in videos.items():
        target = poses / f"{name}.json"
        if target.exists():
            continue
        started = time.perf_counter()
        with OpenCVVideoReader(max_seconds=600).open(video) as stream:
            sized = SizeRecordingStream(stream)
            raw = replace(estimator.estimate(sized), frame_size=sized.size)
        target.write_text(json.dumps(to_artifact(raw)), encoding="utf-8")
        print(f"{name}: {raw.frame_count} fotogramas, detección {raw.detection_rate:.0%}, "
              f"{time.perf_counter() - started:.0f} s", flush=True)


def match(detected: list[int], annotated: list[int], window: int) -> tuple[list[tuple[int, int | None]], list[int]]:
    """Emparejamiento voraz por cercanía: (anotado, detectado o None) y detecciones sin pareja."""
    free = sorted(detected)
    pairs: list[tuple[int, int | None]] = []
    for a in sorted(annotated):
        near = [d for d in free if abs(d - a) <= window]
        if near:
            best = min(near, key=lambda d: abs(d - a))
            free.remove(best)
            pairs.append((a, best))
        else:
            pairs.append((a, None))
    return pairs, free


def clip_stroke(annotations: list[Annotation]) -> StrokeType:
    return "serve" if all(a.stroke == "saque" for a in annotations) else "forehand"


Detector = Callable[[PoseSequence, StrokeType], list[int]]
Chooser = Callable[[PoseSequence, StrokeType], int | None]


def _chosen(segmenter: PeakStrokeSegmenter | WristSpeedSegmenter) -> Chooser:
    def choose(pose: PoseSequence, stroke: StrokeType) -> int | None:
        try:
            return segmenter.segment(pose, "right", stroke).impact_frame
        except NoStrokeFoundError:
            return None
    return choose


def variants() -> dict[str, tuple[bool, Detector, Chooser]]:
    """nombre → (corregir esqueleto, detector de todos los golpes, elección de un golpe)."""
    baseline = WristSpeedSegmenter()
    result: dict[str, tuple[bool, Detector, Chooser]] = {
        "actual (WristSpeed, 1 golpe)": (True, lambda p, s: [baseline.segment(p, "right").impact_frame],
                                         _chosen(baseline)),
    }
    for signal in ("world", "image"):
        for correct in (True, False):
            seg = PeakStrokeSegmenter(signal=signal)
            name = f"Peak {signal}{'' if correct else ' sin corrección'}"
            result[name] = (correct, lambda p, s, seg=seg: [c.impact for c in seg.candidates(p, "right", s)],
                            _chosen(seg))
    return result


@dataclass
class Tally:
    annotated: int = 0
    found: int = 0
    within: int = 0
    detections: int = 0
    chosen_ok: int = 0
    clips: int = 0

    def add(self, other: Tally) -> None:
        for key in vars(self):
            setattr(self, key, getattr(self, key) + getattr(other, key))

    def row(self) -> str:
        recall = self.found / self.annotated if self.annotated else 0
        precision = self.found / self.detections if self.detections else 0
        return (f"exhaustividad {recall:5.0%} ({self.found}/{self.annotated})  precisión {precision:5.0%} "
                f"({self.detections} det.)  impacto en tolerancia {self.within}/{self.found}  "
                f"elegido real {self.chosen_ok}/{self.clips}")


def evaluate_clip(pose: PoseSequence, notes: list[Annotation], detect: Detector, choose: Chooser,
                  verbose: bool) -> Tally:
    stroke = clip_stroke(notes)
    window = round(MATCH_SECONDS * pose.fps)
    limit = ANNOTATED_UNTIL.get(notes[0].clip, pose.frame_count)
    try:
        detected = [d for d in detect(pose, stroke) if d <= limit]
    except NoStrokeFoundError:
        detected = []
    pairs, false = match(detected, [a.impact for a in notes], window)
    tolerance = {a.impact: a.tolerance for a in notes}
    chosen = choose(pose, stroke)
    chosen_ok = chosen is not None and any(abs(chosen - a.impact) <= max(a.tolerance, window // 2) for a in notes)
    tally = Tally(annotated=len(notes), found=sum(d is not None for _, d in pairs),
                  within=sum(d is not None and abs(d - a) <= tolerance[a] for a, d in pairs),
                  detections=len(detected), chosen_ok=int(chosen_ok), clips=1)
    if verbose:
        errors = [None if d is None else d - a for a, d in pairs]
        print(f"    {notes[0].clip:24s} {stroke:8s} errores {errors}  falsos {false}  elegido {chosen}")
    return tally


def evaluate(args: argparse.Namespace) -> None:
    annotations = load_annotations(args.annotations)
    raw = {clip: from_artifact(json.loads((args.poses / f"{clip}.json").read_text(encoding="utf-8")))
           for clip in sorted(annotations)}
    prepared = {flag: {clip: prepare_pose(pose, SkeletonCorrector() if flag else None, args.smoothing)
                       for clip, pose in raw.items()} for flag in (True, False)}
    for name, (correct, detect, choose) in variants().items():
        print(f"\n== {name}")
        groups: dict[str, Tally] = defaultdict(Tally)
        for clip, notes in annotations.items():
            tally = evaluate_clip(prepared[correct][clip], notes, detect, choose, args.verbose)
            material = "velocidad real" if clip in REAL_SPEED else "cámara lenta/editado"
            kind = "saque" if clip_stroke(notes) == "serve" else "fondo"
            for group in ("TOTAL", f"{kind} · {material}"):
                groups[group].add(tally)
        for group in sorted(groups, key=lambda g: (g != "TOTAL", g)):
            print(f"  {group:30s} {groups[group].row()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("command", choices=["download", "extract", "evaluate"])
    parser.add_argument("--annotations", type=Path, default=UPSTREAM / "golpes.csv")
    parser.add_argument("--videos", type=Path, default=BENCH / "videos")
    parser.add_argument("--poses", type=Path, default=BENCH / "poses")
    parser.add_argument("--model", type=Path, default=ROOT / "models" / "pose_landmarker_full.task")
    parser.add_argument("--smoothing", type=float, default=0.1)
    parser.add_argument("-v", "--verbose", action="store_true", help="errores y falsos positivos por clip")
    args = parser.parse_args()
    {"download": download, "extract": extract, "evaluate": evaluate}[args.command](args)


if __name__ == "__main__":
    main()
