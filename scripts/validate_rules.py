#!/usr/bin/env python3
"""Valida las reglas del YAML con golpes de profesionales, dejando fuera a cada jugador.

Cada profesional se analiza como si fuera un usuario: su sesión es la mediana de sus golpes (golpe y vista)
y se pasa por las reglas. Un profesional no debería disparar reglas de error. Para cada condición se calcula
además la cota hasta la que puede llegar el umbral sin castigar a profesionales, y se comprueba dejando fuera
a cada jugador si esa cota generaliza (worker/.../knowledge/pro_validation.py).

    python scripts/validate_rules.py extract    # baja los clips que falten y extrae su pose (una vez)
    python scripts/validate_rules.py evaluate   # métricas, reglas y cotas; segundos

Golpes:
- tennis-analyzer (MIT): references/golpes.csv (a mano) y golpes_auto.csv (impacto automático, tipo y vista
  verificados a ojo), con el jugador de manifest.csv. Solo para evaluar, en data/ (fuera de git). Se quitan
  la cámara lenta editada y las compilaciones: las métricas con ventanas en segundos no valen ahí.
- Referencias CC BY de videos-tennis/creative-commons/referencias/manifest.json (impacto verificado a ojo o,
  si no lo hay, el del segmentador más el audio, como en producción).

No sustituye a la calibración con principiantes: solo dice qué umbrales castigan golpes buenos.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from bench_segmentation import BENCH, UPSTREAM, download_clips, extract_poses  # noqa: E402

from tennis_worker.contracts import CameraView, StrokeType  # noqa: E402
from tennis_worker.pipeline.audio import AudioImpactRefiner, PyAvAudioReader  # noqa: E402
from tennis_worker.pipeline.biomechanics.base import MetricContext  # noqa: E402
from tennis_worker.pipeline.biomechanics.registry import default_registry  # noqa: E402
from tennis_worker.pipeline.biomechanics.robustness import TemporalRobustness  # noqa: E402
from tennis_worker.pipeline.knowledge.engine import RuleEngine  # noqa: E402
from tennis_worker.pipeline.knowledge.pro_validation import (  # noqa: E402
    MIN_PLAYERS,
    ConditionCheck,
    ProStroke,
    Session,
    check_conditions,
    fire_counts,
    sessions,
)
from tennis_worker.pipeline.knowledge.repository import YamlKnowledgeBaseRepository  # noqa: E402
from tennis_worker.pipeline.orchestrator import prepare_pose  # noqa: E402
from tennis_worker.pipeline.pose.serialization import from_artifact  # noqa: E402
from tennis_worker.pipeline.pose.skeleton_correction import SkeletonCorrector  # noqa: E402
from tennis_worker.pipeline.segmentation import PeakStrokeSegmenter  # noqa: E402
from tennis_worker.pipeline.types import PoseSequence  # noqa: E402

CC = ROOT.parent / "videos-tennis" / "creative-commons" / "referencias"
CC_POSES = ROOT / "data" / "bench" / "rules" / "poses"
STROKES: dict[str, StrokeType] = {"derecha": "forehand", "reves_2manos": "backhand", "saque": "serve"}
VIEWS: dict[str, CameraView] = {"lateral": "side", "frontal": "front", "espalda": "back"}
# Cámara lenta editada o compilaciones de varios jugadores (tennis-analyzer, docs/hallazgos.md).
EXCLUDED = {"fh_djokovic_1000fps", "bh_djokovic_1000fps", "fh_compilacion_espalda", "fh_roddick_lat"}
MIN_CONFIDENCE = 0.5  # el de producción (MIN_METRIC_CONFIDENCE)


@dataclass(frozen=True)
class Annotated:
    clip: str  # nombre de la pose
    video: Path
    impact: int | None  # None = el del segmentador y el audio
    tolerance: int | None  # fotogramas; None = impacto automático
    stroke: StrokeType
    view: CameraView
    player: str


def upstream_strokes(directory: Path) -> list[Annotated]:
    with (directory / "manifest.csv").open(newline="", encoding="utf-8") as f:
        players = {row["nombre"]: row["jugador"] for row in csv.DictReader(f)}
    result = []
    for name, tolerance_col in (("golpes.csv", "tolerancia"), ("golpes_auto.csv", None)):
        with (directory / name).open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["clip"] in EXCLUDED or row["golpe"] not in STROKES or row["vista"] not in VIEWS:
                    continue
                result.append(Annotated(row["clip"], BENCH / "videos" / f"{row['clip']}.mp4", int(row["impacto"]),
                                        int(row[tolerance_col]) if tolerance_col else None,
                                        STROKES[row["golpe"]], VIEWS[row["vista"]], players[row["clip"]]))
    return result


def cc_player(clip: dict[str, str]) -> str:
    """El nombre del rótulo («Derecha de Joao Fonseca…») o, si no lo hay, el video de origen."""
    if named := re.search(r" de ([A-Z]\w+(?: [A-Z]\w+)+)", clip["label"]):
        return named.group(1)
    video = re.search(r"(?:v=|shorts/)([\w-]{11})", clip["origin"])
    return f"video {video.group(1) if video else clip['file']}"


def cc_strokes(directory: Path) -> list[Annotated]:
    manifest = directory / "manifest.json"
    if not manifest.exists():
        return []
    clips = json.loads(manifest.read_text(encoding="utf-8"))["clips"]
    return [Annotated(f"cc_{Path(c['file']).stem}", directory / c["file"], c.get("impact_frame"),
                      1 if "impact_frame" in c else None, c["stroke"], c["view"], cc_player(c))
            for c in clips if c["handedness"] == "right"]


def extract(args: argparse.Namespace) -> None:
    upstream = upstream_strokes(args.annotations)
    download_clips(sorted({a.clip for a in upstream}), args.annotations / "manifest.csv", BENCH / "videos")
    extract_poses({a.clip: a.video for a in upstream}, BENCH / "poses", args.model)
    extract_poses({a.clip: a.video for a in cc_strokes(args.cc)}, CC_POSES, args.model)


@dataclass(frozen=True)
class Measured:
    stroke: ProStroke
    wrist_speed: float  # torsos/s en el impacto: separa velocidad real de cámara lenta


def measure(annotated: list[Annotated], smoothing: float) -> list[Measured]:
    registry = default_registry()
    metrics = TemporalRobustness(registry)
    segmenter = PeakStrokeSegmenter()
    refiner = AudioImpactRefiner(PyAvAudioReader(), rebuild=segmenter.segment_at)
    poses: dict[str, PoseSequence] = {}
    result = []
    for a in annotated:
        if a.clip not in poses:
            folder = CC_POSES if a.clip.startswith("cc_") else BENCH / "poses"
            raw = from_artifact(json.loads((folder / f"{a.clip}.json").read_text(encoding="utf-8")))
            poses[a.clip] = prepare_pose(raw, SkeletonCorrector(), smoothing)
        pose = poses[a.clip]
        if a.impact is None:
            refined = refiner.refine(segmenter.segment(pose, "right", a.stroke), pose, a.video, "right")
            segment, uncertainty = refined.segment, (1 / pose.fps if refined.source == "audio" else 0.0)
        else:
            segment = segmenter.segment_at(pose, "right", a.impact)
            uncertainty = max(1, a.tolerance) / pose.fps if a.tolerance is not None else 0.0
        values = metrics.compute_all(a.stroke, MetricContext(pose, segment, "right", uncertainty))
        speed = segmenter.wrist_speed(pose, "right")
        near = round(0.2 * pose.fps)
        peak = float(speed[max(0, segment.impact_frame - near):segment.impact_frame + near + 1].max())
        result.append(Measured(ProStroke(a.player, a.stroke, a.view, values, f"{a.clip}@{segment.impact_frame}"),
                               peak))
    return result


def _pct(rate: float) -> str:
    return "  -  " if math.isnan(rate) else f"{rate:5.0%}"


def print_inventory(all_sessions: list[Session]) -> None:
    print("Sesiones (jugador · golpes) por golpe y vista:")
    groups: dict[tuple[str, str], list[Session]] = defaultdict(list)
    for s in all_sessions:
        groups[(s.stroke, s.view)].append(s)
    for (stroke, view), group in sorted(groups.items()):
        players = ", ".join(f"{s.player} {s.strokes}" for s in group)
        lopo = "dejando uno fuera" if len(group) > MIN_PLAYERS else ("solo cota" if len(group) == MIN_PLAYERS
                                                                     else "pocos jugadores")
        print(f"  {stroke:9s} {view:5s} {len(group)} jugadores ({lopo}): {players}")


def print_reliability(strokes: list[ProStroke]) -> None:
    """Métricas que casi nunca se miden con fiabilidad: sus reglas apenas se pueden validar (ni disparar)."""
    print(f"\nMétricas fiables (confianza >= {MIN_CONFIDENCE}) por golpe:")
    for stroke_type in ("forehand", "backhand", "serve"):
        mine = [s for s in strokes if s.stroke == stroke_type]
        ids = sorted({k for s in mine for k in s.metrics})
        cells = [f"{k} {sum(s.metrics[k].confidence >= MIN_CONFIDENCE for s in mine if k in s.metrics) / len(mine):.0%}"
                 for k in ids]
        print(f"  {stroke_type} ({len(mine)} golpes): " + ", ".join(cells))


def print_fire_rates(strokes: list[ProStroke], all_sessions: list[Session], engine: RuleEngine,
                     repo: YamlKnowledgeBaseRepository) -> None:
    print("\nReglas que saltan con profesionales (por golpe suelto · por sesión):")
    for stroke_type in ("forehand", "backhand", "serve"):
        kb = repo.load(stroke_type)  # type: ignore[arg-type]
        by_stroke = fire_counts(kb, ((s.source, s.view, s.metrics) for s in strokes if s.stroke == stroke_type),
                                engine)
        by_session = fire_counts(kb, ((f"{s.player}/{s.view}", s.view, s.metrics)
                                      for s in all_sessions if s.stroke == stroke_type), engine)
        print(f"\n  {kb.label}")
        for rule in kb.rules:
            g, se = by_stroke[rule.id], by_session[rule.id]
            kind = "fortaleza" if rule.is_strength else "error"
            who = f"  ← {', '.join(se.who)}" if se.fired and not rule.is_strength else ""
            print(f"    {rule.id:16s} {kind:9s} golpes {_pct(g.rate)} ({g.fired:2d}/{g.evaluated:2d})   "
                  f"sesiones {_pct(se.rate)} ({se.fired}/{se.evaluated}){who}")


def print_bounds(checks: list[ConditionCheck]) -> None:
    print("\nCotas de los profesionales por condición (umbral → cota; ✗ = el umbral castiga a profesionales;"
          "\n· = condición combinada con otras: la juzga la regla entera):")
    for c in checks:
        if c.bound is None:
            continue
        op = "<" if c.side == "low" else ">"
        flagged = [h.player for h in c.held_out if h.flagged]
        lopo = (f"dejando uno fuera: {len(c.held_out) - len(flagged)}/{len(c.held_out)} dentro"
                + (f" (fuera: {', '.join(flagged)})" if flagged else "")) if c.held_out else "sin dejar uno fuera"
        mark = "✗" if c.too_strict else ("·" if c.combined else " ")
        print(f"  {mark} {c.rule_id:16s} {c.view:5s} {c.condition.metric:28s} {op} {c.threshold:7.2f}  "
              f"cota {c.bound:7.2f}  ({c.players} jug.; {lopo})")
    tested = [h for c in checks if not c.combined for h in c.held_out]
    if tested:
        inside = sum(not h.flagged for h in tested)
        print(f"\n  Dejando uno fuera, profesional dentro de la cota: {inside}/{len(tested)} "
              f"({inside / len(tested):.0%})")


def evaluate(args: argparse.Namespace) -> None:
    annotated = upstream_strokes(args.annotations) + cc_strokes(args.cc)
    measured = measure(annotated, args.smoothing)
    slow = [m for m in measured if m.wrist_speed < args.min_wrist_speed]
    if args.verbose:
        print("Velocidad de la muñeca en el impacto (torsos/s) por clip:")
        speeds: dict[str, list[float]] = defaultdict(list)
        for m in measured:
            speeds[m.stroke.source.split("@")[0]].append(m.wrist_speed)
        for clip, values in sorted(speeds.items()):
            print(f"  {clip:28s} mediana {sorted(values)[len(values) // 2]:5.1f}  ({len(values)} golpes)")
    if slow:
        print(f"Quitados por cámara lenta (muñeca < {args.min_wrist_speed} torsos/s): "
              f"{dict(Counter(m.stroke.source.split('@')[0] for m in slow))}\n")
    strokes = [m.stroke for m in measured if m.wrist_speed >= args.min_wrist_speed]
    all_sessions = sessions(strokes, MIN_CONFIDENCE)
    engine = RuleEngine(min_confidence=MIN_CONFIDENCE)
    repo = YamlKnowledgeBaseRepository(args.knowledge)
    print_inventory(all_sessions)
    print_reliability(strokes)
    print_fire_rates(strokes, all_sessions, engine, repo)
    checks = [c for stroke_type in ("forehand", "backhand", "serve")
              for c in check_conditions(repo.load(stroke_type), all_sessions)]  # type: ignore[arg-type]
    print_bounds(checks)
    if args.metrics:
        print_session_metrics(all_sessions)


def print_session_metrics(all_sessions: list[Session]) -> None:
    print("\nMedianas por sesión:")
    for s in all_sessions:
        values = "  ".join(f"{k}={v.value:.2f}" for k, v in s.metrics.items())
        print(f"  {s.stroke}/{s.view} {s.player} ({s.strokes}): {values}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # consola de Windows
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("command", choices=["extract", "evaluate"])
    parser.add_argument("--annotations", type=Path, default=UPSTREAM, help="carpeta references/ de tennis-analyzer")
    parser.add_argument("--cc", type=Path, default=CC, help="carpeta con el manifest.json de referencias CC BY")
    parser.add_argument("--knowledge", type=Path, default=ROOT / "worker" / "knowledge_base")
    parser.add_argument("--model", type=Path, default=ROOT / "models" / "pose_landmarker_full.task")
    parser.add_argument("--smoothing", type=float, default=0.1)
    parser.add_argument("--min-wrist-speed", type=float, default=6.0,
                        help="torsos/s en el impacto; por debajo se toma por cámara lenta y se quita")
    parser.add_argument("--metrics", action="store_true", help="imprime las medianas de cada sesión")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    {"extract": extract, "evaluate": evaluate}[args.command](args)


if __name__ == "__main__":
    main()
