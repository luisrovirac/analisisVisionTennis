"""Serializa un PoseSequence según contracts/pose_sequence.schema.json."""

from __future__ import annotations

from typing import Any

import numpy as np

from tennis_worker.contracts import POSE_SCHEMA_VERSION
from tennis_worker.pipeline.types import LANDMARK_COUNT, PoseSequence


def to_artifact(pose: PoseSequence) -> dict[str, Any]:
    image = np.round(np.nan_to_num(pose.image), 5).tolist()
    world = np.round(np.nan_to_num(pose.world), 5).tolist()
    visibility = np.round(pose.visibility, 3).tolist()
    frames = [
        {"index": i, "detected": bool(pose.detected[i]), "image": image[i], "world": world[i],
         "visibility": visibility[i]}
        for i in range(pose.frame_count)
    ]
    artifact: dict[str, Any] = {"schema_version": POSE_SCHEMA_VERSION, "fps": pose.fps,
                                "landmark_count": LANDMARK_COUNT, "frames": frames}
    if pose.frame_size is not None:  # opcional (aditivo): permite verificar la orientación en el visor
        artifact["width"], artifact["height"] = pose.frame_size
    return artifact


def from_artifact(data: dict[str, Any]) -> PoseSequence:
    """Inversa de to_artifact: el equipo de ML y el de biomecánica trabajan con poses/<id>.json offline.
    Los fotogramas sin detección vuelven como NaN, igual que los produce el estimador."""
    frames = data["frames"]
    detected = np.array([f["detected"] for f in frames], dtype=bool)
    image = np.array([f["image"] for f in frames], dtype=np.float64).reshape(len(frames), LANDMARK_COUNT, 3)
    world = np.array([f["world"] for f in frames], dtype=np.float64).reshape(len(frames), LANDMARK_COUNT, 3)
    visibility = np.array([f["visibility"] for f in frames], dtype=np.float64).reshape(len(frames), LANDMARK_COUNT)
    image[~detected] = np.nan
    world[~detected] = np.nan
    size = (int(data["width"]), int(data["height"])) if "width" in data and "height" in data else None
    return PoseSequence(fps=float(data["fps"]), image=image, world=world, visibility=visibility, detected=detected,
                        frame_size=size)
