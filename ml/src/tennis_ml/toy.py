"""Dataset sintético de poses (sin videos) para probar el pipeline de punta a punta en segundos.

No sirve para entrenar un modelo real: cada clase es una trayectoria de muñeca simplificada.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from tennis_worker.pipeline.pose.serialization import to_artifact
from tennis_worker.pipeline.types import LANDMARK_COUNT, PoseSequence

from tennis_ml.datasets import POSE_SUFFIX

Trajectory = Callable[[NDArray[np.float64]], NDArray[np.float64]]  # t ∈ [0,1] → (T, 3) muñeca derecha

# Esqueleto en reposo (metros, centrado en la cadera, y hacia abajo), por índice de MediaPipe.
_BASE = {0: (0, -0.65, 0), 11: (0.18, -0.45, 0), 12: (-0.18, -0.45, 0), 13: (0.25, -0.2, 0), 14: (-0.25, -0.2, 0),
         15: (0.3, 0.0, 0), 16: (-0.3, 0.0, 0), 23: (0.1, 0.0, 0), 24: (-0.1, 0.0, 0), 25: (0.1, 0.45, 0),
         26: (-0.1, 0.45, 0), 27: (0.1, 0.85, 0), 28: (-0.1, 0.85, 0)}

TRAJECTORIES: dict[str, Trajectory] = {
    "forehand": lambda t: np.stack([-0.6 + 1.0 * t, 0.1 - 0.5 * t, 0.3 * np.sin(np.pi * t)], axis=1),
    "backhand": lambda t: np.stack([0.5 - 1.0 * t, 0.1 - 0.4 * t, 0.3 * np.sin(np.pi * t)], axis=1),
    "serve": lambda t: np.stack([-0.2 + 0.0 * t, -1.0 * np.sin(np.pi * t), 0.2 * t], axis=1),
    "volley": lambda t: np.stack([-0.3 + 0.0 * t, -0.2 + 0.0 * t, 0.35 * np.sin(np.pi * t)], axis=1),
    "other": lambda t: np.stack([-0.2 + 0.0 * t, -0.9 + 1.1 * t, 0.1 * t], axis=1),
}


def make_toy(out_dir: Path, players: int = 10, clips_per_class: int = 3, seed: int = 0) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    for player in range(players):
        scale = rng.uniform(0.85, 1.15)  # estatura distinta por jugador
        for label, trajectory in TRAJECTORIES.items():
            for clip in range(clips_per_class):
                pose = _clip(trajectory, scale, rng)
                path = out_dir / f"{label}_p{player}_{clip}{POSE_SUFFIX}"
                path.write_text(json.dumps(to_artifact(pose)), encoding="utf-8")


def _clip(trajectory: Trajectory, scale: float, rng: np.random.Generator) -> PoseSequence:
    frames = int(rng.integers(40, 90))
    t = np.clip(np.linspace(0, 1, frames) ** rng.uniform(0.8, 1.25), 0, 1)
    world = np.zeros((frames, LANDMARK_COUNT, 3))
    for index, position in _BASE.items():
        world[:, index] = position
    world[:, 16] = trajectory(t)
    world[:, 14] = (world[:, 12] + world[:, 16]) / 2  # codo entre hombro y muñeca
    world = world * scale + rng.normal(0, 0.01, world.shape)
    image = world * 0.4 + 0.5
    return PoseSequence(fps=30.0, image=image, world=world, visibility=np.ones((frames, LANDMARK_COUNT)),
                        detected=np.ones(frames, dtype=bool))
