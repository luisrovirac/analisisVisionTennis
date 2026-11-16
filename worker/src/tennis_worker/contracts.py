"""Modelos espejo de contracts/*.schema.json. Cualquier cambio aquí exige subir schema_version."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Versiones por contrato: cambian solo ante cambios incompatibles (ver Docs/CONTRATOS.md).
REPORT_SCHEMA_VERSION = "1.0"
POSE_SCHEMA_VERSION = "1.0"
JOB_SCHEMA_VERSIONS = ("1.0", "1.1")  # 1.1: stroke_type opcional (detección automática)

StrokeType = Literal["forehand", "backhand", "serve", "volley"]
Handedness = Literal["right", "left"]
CameraView = Literal["side", "front", "back"]
Category = Literal["fundamental", "sincronizacion"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class JobMessage(_Frozen):
    model_config = ConfigDict(frozen=True, extra="ignore")  # Postel: tolera campos nuevos

    schema_version: Literal["1.0", "1.1"]
    analysis_id: str
    video_key: str
    stroke_type: StrokeType | None = None  # None = que lo detecte el clasificador
    handedness: Handedness
    camera_view: CameraView


class Segment(_Frozen):
    fps: float
    start_frame: int
    backswing_frame: int
    impact_frame: int
    end_frame: int


class MetricValue(_Frozen):
    id: str
    label: str
    value: float
    unit: str
    confidence: float = Field(ge=0.0, le=1.0)


class Finding(_Frozen):
    rule_id: str
    rank: int = Field(ge=1)
    category: Category
    title: str
    explanation: str
    correction: str
    drills: list[str]
    evidence: dict[str, float]


class Quality(_Frozen):
    pose_detection_rate: float = Field(ge=0.0, le=1.0)
    warnings: list[str]


class Classification(_Frozen):
    """Cómo se decidió el golpe: de dónde sale (usuario o modelo) y qué modelo/datos lo produjeron."""

    stroke: StrokeType
    confidence: float = Field(ge=0.0, le=1.0)
    classifier_id: str
    model_version: str | None = None
    dataset_hash: str | None = None


class Report(_Frozen):
    schema_version: str = REPORT_SCHEMA_VERSION
    stroke_type: StrokeType
    segment: Segment
    metrics: list[MetricValue]
    findings: list[Finding]
    quality: Quality
    knowledge_base_version: str
    classification: Classification | None = None  # campo opcional: no cambia schema_version
