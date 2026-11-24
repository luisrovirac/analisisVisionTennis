"""Modelos espejo de contracts/*.schema.json. Un cambio incompatible exige subir schema_version;
añadir un campo opcional (revisión 1.1 del reporte) no lo cambia."""

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
    # 1.2: índices MediaPipe (0-32) de los landmarks en los que se apoya la métrica; el visor los resalta.
    landmarks: list[int] | None = None


class Finding(_Frozen):
    rule_id: str
    rank: int = Field(ge=1)
    category: Category
    title: str
    explanation: str
    correction: str
    drills: list[str]
    evidence: dict[str, float]
    validated: bool = False  # 1.1: aprobada por un validador (False = provisional)
    # 1.3: heuristic_unvalidated | expert_reviewed | empirically_validated (sin nombres de expertos)
    validation_status: str | None = None


class Strength(_Frozen):
    """1.1: punto fuerte. No es la ausencia de un error: tiene su propia regla y umbral."""

    rule_id: str
    rank: int = Field(ge=1)
    category: Category
    title: str
    why_it_matters: str
    how_to_maintain: str
    evidence: dict[str, float]
    confidence: float = Field(ge=0.0, le=1.0)
    validated: bool = False
    validation_status: str | None = None  # 1.3


class Diagnosis(_Frozen):
    summary: str
    root_cause_rule_id: str | None
    contributing_factors: list[str]
    strengths_to_leverage: list[str]
    limitations: list[str]
    unmeasurable: list[str]


class DrillPlanItem(_Frozen):
    week: int = Field(ge=1)
    focus_rule_id: str
    focus: str
    drills: list[str]


class DrillPlan(_Frozen):
    weeks: int = Field(ge=1)
    reanalyze_after_weeks: int = Field(ge=1)
    items: list[DrillPlanItem]


ReliabilityLevel = Literal["high", "medium", "low"]


class Reliability(_Frozen):
    score: float = Field(ge=0.0, le=1.0)
    level: ReliabilityLevel
    reasons: list[str]


class KnowledgeStatus(_Frozen):
    status: Literal["provisional", "partially_validated", "validated"]
    validated_rules: int = Field(ge=0)
    total_rules: int = Field(ge=0)


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


PhaseName = Literal["preparation", "acceleration", "follow_through"]


class ReferenceUsed(_Frozen):
    ref_id: str
    label: str


class PhaseSimilarity(_Frozen):
    """Similitud (DTW sobre ángulos articulares) de una fase del golpe con la de la referencia."""

    phase: PhaseName
    status: Literal["ok", "low_confidence", "too_short"]
    similarity: float | None = Field(default=None, ge=0.0, le=100.0)  # None si no se puntúa
    confidence: float = Field(ge=0.0, le=1.0)


class ReferenceComparison(_Frozen):
    """1.2: comparación INFORMATIVA con golpes de referencia. No genera errores ni puntos fuertes."""

    status: Literal["ok", "partial", "low_confidence", "no_references"]
    references_used: list[ReferenceUsed] = Field(default_factory=list)
    overall_similarity: float | None = Field(default=None, ge=0.0, le=100.0)
    phases: list[PhaseSimilarity] = Field(default_factory=list)
    note: str = ""


class Report(_Frozen):
    schema_version: str = REPORT_SCHEMA_VERSION
    stroke_type: StrokeType
    segment: Segment
    metrics: list[MetricValue]
    findings: list[Finding]
    quality: Quality
    knowledge_base_version: str
    classification: Classification | None = None  # campo opcional: no cambia schema_version
    # Revisión 1.1 (aditiva): todos opcionales, schema_version sigue en 1.0.
    strengths: list[Strength] = Field(default_factory=list)
    diagnosis: Diagnosis | None = None
    drill_plan: DrillPlan | None = None
    reliability: Reliability | None = None
    knowledge_status: KnowledgeStatus | None = None
    # Revisión 1.2 (aditiva): comparación con referentes por DTW.
    reference_comparison: ReferenceComparison | None = None
