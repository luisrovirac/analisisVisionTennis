"""Índices de MediaPipe Pose y selección del lado dominante."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np

from tennis_worker.contracts import Handedness
from tennis_worker.pipeline.types import LANDMARK_COUNT

# Pares izquierda/derecha de MediaPipe Pose (cara, brazos, manos, piernas y pies).
MIRROR_PAIRS: tuple[tuple[int, int], ...] = (
    (1, 4), (2, 5), (3, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16), (17, 18),
    (19, 20), (21, 22), (23, 24), (25, 26), (27, 28), (29, 30), (31, 32),
)
# Permutación que intercambia izquierda y derecha: points[:, MIRROR_ORDER] voltea el esqueleto.
MIRROR_ORDER = np.arange(LANDMARK_COUNT)
for _a, _b in MIRROR_PAIRS:
    MIRROR_ORDER[_a], MIRROR_ORDER[_b] = _b, _a


class Landmark(IntEnum):
    NOSE = 0
    LEFT_SHOULDER = 11
    RIGHT_SHOULDER = 12
    LEFT_ELBOW = 13
    RIGHT_ELBOW = 14
    LEFT_WRIST = 15
    RIGHT_WRIST = 16
    LEFT_HIP = 23
    RIGHT_HIP = 24
    LEFT_KNEE = 25
    RIGHT_KNEE = 26
    LEFT_ANKLE = 27
    RIGHT_ANKLE = 28


@dataclass(frozen=True)
class Side:
    shoulder: int
    elbow: int
    wrist: int
    hip: int
    knee: int
    ankle: int


LEFT = Side(
    Landmark.LEFT_SHOULDER, Landmark.LEFT_ELBOW, Landmark.LEFT_WRIST,
    Landmark.LEFT_HIP, Landmark.LEFT_KNEE, Landmark.LEFT_ANKLE,
)
RIGHT = Side(
    Landmark.RIGHT_SHOULDER, Landmark.RIGHT_ELBOW, Landmark.RIGHT_WRIST,
    Landmark.RIGHT_HIP, Landmark.RIGHT_KNEE, Landmark.RIGHT_ANKLE,
)


@dataclass(frozen=True)
class BodySides:
    dominant: Side
    non_dominant: Side


_SIDES: dict[Handedness, BodySides] = {
    "right": BodySides(dominant=RIGHT, non_dominant=LEFT),
    "left": BodySides(dominant=LEFT, non_dominant=RIGHT),
}


def body_sides(handedness: Handedness) -> BodySides:
    return _SIDES[handedness]
