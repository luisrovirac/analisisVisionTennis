"""Etapa prepare: convierte cada fuente de datos en un único data/labels.csv con partición por jugador.

Fuentes (params.yaml → dataset.name, carpeta data/raw/<name>):
- thetis: THETIS RGB tal como se descarga (VIDEO_RGB/<clase>/p<jugador>_..._s<n>.avi).
- own: videos propios + labels.csv (video, stroke, handedness, player). El de la calibración con entrenadores.
- toy: poses sintéticas (*.pose.json), generadas con `python -m tennis_ml make-toy`. Para pruebas.
"""

from __future__ import annotations

import csv
import random
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

# THETIS (12 acciones) → clases del producto. El smash no es un golpe soportado: "other".
THETIS_CLASSES = {
    "backhand": "backhand", "backhand2hands": "backhand", "backhand_slice": "backhand",
    "backhand_volley": "volley", "forehand_volley": "volley",
    "flat_service": "serve", "kick_service": "serve", "slice_service": "serve",
    "forehand_flat": "forehand", "forehand_openstands": "forehand", "forehand_slice": "forehand",
    "smash": "other",
}
VIDEO_EXTENSIONS = {".avi", ".mp4", ".mov", ".mkv"}
POSE_SUFFIX = ".pose.json"
FIELDS = ["id", "source", "label", "player", "handedness", "split"]


@dataclass(frozen=True)
class Sample:
    id: str
    source: str  # relativo a data/raw/<dataset>
    label: str
    player: str
    handedness: str = "right"
    split: str = ""


def prepare(dataset: str, raw_dir: Path, seed: int, fractions: dict[str, float]) -> list[Sample]:
    loaders: dict[str, Callable[[Path], list[Sample]]] = {"thetis": _thetis, "own": _own, "toy": _toy}
    if dataset not in loaders:
        raise ValueError(f"dataset desconocido {dataset!r}; usa {sorted(loaders)}")
    samples = loaders[dataset](raw_dir)
    if not samples:
        raise ValueError(f"no se encontró ningún clip en {raw_dir}")
    splits = split_by_player({s.player for s in samples}, seed, fractions)
    return [replace(s, split=splits[s.player]) for s in sorted(samples, key=lambda s: s.id)]


def split_by_player(players: Iterable[str], seed: int, fractions: dict[str, float]) -> dict[str, str]:
    """Reparto determinista de jugadores (no de clips) entre particiones: evita fugas de identidad."""
    ordered = sorted(players)
    random.Random(seed).shuffle(ordered)
    total = sum(fractions.values())
    result, start = {}, 0.0
    for split, fraction in fractions.items():
        end = start + fraction / total
        for player in ordered[round(start * len(ordered)):round(end * len(ordered))]:
            result[player] = split
        start = end
    return result


def _thetis(raw_dir: Path) -> list[Sample]:
    samples = []
    for path in sorted(raw_dir.rglob("*")):
        action = path.parent.name.lower()
        player = re.search(r"p(\d+)", path.stem, re.IGNORECASE)
        if path.suffix.lower() in VIDEO_EXTENSIONS and action in THETIS_CLASSES and player:
            source = path.relative_to(raw_dir).as_posix()
            samples.append(Sample(id=_slug(source), source=source, label=THETIS_CLASSES[action],
                                  player=f"thetis-{int(player.group(1))}"))
    return samples


def _own(raw_dir: Path) -> list[Sample]:
    with (raw_dir / "labels.csv").open(encoding="utf-8", newline="") as fh:
        return [Sample(id=_slug(row["video"]), source=row["video"], label=row["stroke"],
                       player=row["player"], handedness=row.get("handedness") or "right")
                for row in csv.DictReader(fh)]


def _toy(raw_dir: Path) -> list[Sample]:
    """Archivos <clase>_<jugador>_<n>.pose.json."""
    samples = []
    for path in sorted(raw_dir.glob(f"*{POSE_SUFFIX}")):
        label, player, _ = path.name.removesuffix(POSE_SUFFIX).split("_")
        samples.append(Sample(id=path.name.removesuffix(POSE_SUFFIX), source=path.name, label=label, player=player))
    return samples


def _slug(source: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", Path(source).with_suffix("").as_posix()).strip("-").lower()


def write_labels(samples: list[Sample], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(asdict(s) for s in samples)


def read_labels(path: Path) -> list[Sample]:
    with path.open(encoding="utf-8", newline="") as fh:
        return [Sample(**row) for row in csv.DictReader(fh)]
