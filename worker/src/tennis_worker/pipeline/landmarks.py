"""Índices de MediaPipe Pose y selección del lado dominante."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from tennis_worker.contracts import Handedness


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
