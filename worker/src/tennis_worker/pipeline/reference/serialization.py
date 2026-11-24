"""Serializa golpes de referencia según contracts/reference_stroke.schema.json."""

from __future__ import annotations

from typing import Any

from tennis_worker.contracts import Segment
from tennis_worker.pipeline.pose.postprocess import interpolate_gaps
from tennis_worker.pipeline.pose.serialization import from_artifact, to_artifact
from tennis_worker.pipeline.reference.comparator import ReferenceStroke

REFERENCE_SCHEMA_VERSION = "1.0"
INDEX_KEY = "references/index.json"


def reference_key(stroke: str, ref_id: str) -> str:
    return f"references/{stroke}/{ref_id}.json"


def to_reference_artifact(ref: ReferenceStroke) -> dict[str, Any]:
    return {
        "schema_version": REFERENCE_SCHEMA_VERSION, "ref_id": ref.ref_id, "label": ref.label,
        "stroke": ref.stroke, "camera_view": ref.camera_view, "handedness": ref.handedness,
        "source": ref.source, "segment": ref.segment.model_dump(), "pose": to_artifact(ref.pose),
    }


def from_reference_artifact(data: dict[str, Any]) -> ReferenceStroke:
    # from_artifact devuelve como NaN los fotogramas sin detección; se rellenan igual que al registrar
    # (interpolate_gaps) para comparar exactamente la pose que se validó, sin NaN en las características.
    return ReferenceStroke(
        ref_id=str(data["ref_id"]), label=str(data["label"]), stroke=data["stroke"],
        camera_view=data["camera_view"], handedness=data["handedness"], source=str(data.get("source", "")),
        pose=interpolate_gaps(from_artifact(data["pose"])), segment=Segment.model_validate(data["segment"]),
    )


def index_entry(ref: ReferenceStroke, registered_at: str) -> dict[str, str]:
    return {"ref_id": ref.ref_id, "label": ref.label, "stroke": ref.stroke, "camera_view": ref.camera_view,
            "key": reference_key(ref.stroke, ref.ref_id), "registered_at": registered_at}


def upsert_index(index: dict[str, Any] | None, entry: dict[str, str]) -> dict[str, Any]:
    """El índice con `entry` añadida (o reemplazada si ya existía ese ref_id para el golpe)."""
    refs = [r for r in (index or {}).get("references", [])
            if not (r.get("ref_id") == entry["ref_id"] and r.get("stroke") == entry["stroke"])]
    return {"schema_version": REFERENCE_SCHEMA_VERSION, "references": [*refs, entry]}
