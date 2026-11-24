"""Configuración del worker: toda viene del entorno y se valida al arrancar (fail fast)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

KNOWN_CLASSIFIERS = frozenset({"user", "pose_seq", "yolo"})


def _weights(raw: str) -> dict[str, float]:
    weights: dict[str, float] = {}
    for item in filter(None, (part.strip() for part in raw.split(","))):
        name, sep, number = item.partition("=")
        if not sep or name.strip() not in KNOWN_CLASSIFIERS:
            raise ValueError(f"peso inválido {item!r}; formato: pose_seq=2,yolo=1")
        weights[name.strip()] = float(number)
    return weights


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    database_url: SecretStr
    redis_url: str
    job_stream: str = "analysis_jobs"
    job_group: str = "analysis_workers"
    job_dlq_stream: str = ""  # vacío = "<job_stream>:dlq"

    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: SecretStr
    s3_bucket: str
    s3_region: str = "us-east-1"
    s3_use_ssl: bool = False

    pose_model_path: Path
    # Estimador de pose. mediapipe (por defecto) o rtmpose_motionbert (fase 2: RTMPose 2D + MotionBERT 3D;
    # requiere MOTIONBERT_MODEL_VERSION, ver Docs/BASE_CONOCIMIENTO.md, "Comparar estimadores de pose").
    pose_estimator: Literal["mediapipe", "rtmpose_motionbert"] = "mediapipe"
    motionbert_model_version: str = ""
    knowledge_base_dir: Path  # semilla YAML (y fuente directa si knowledge_source = yaml)
    # postgres: versión publicada desde el portal de expertos (por defecto); yaml: solo los archivos.
    knowledge_source: Literal["postgres", "yaml"] = "postgres"
    knowledge_refresh_seconds: float = Field(default=15.0, gt=0)

    max_video_seconds: float = Field(default=30.0, gt=0)
    max_attempts: int = Field(default=3, ge=1)
    job_claim_idle_seconds: int = Field(default=600, ge=10)
    min_pose_detection_rate: float = Field(default=0.6, gt=0, le=1)
    min_metric_confidence: float = Field(default=0.5, ge=0, le=1)
    # Robustez de la medición (fase 1 de la mejora de la pose 2D; ver Docs/BASE_CONOCIMIENTO.md).
    # Señal del segmentador: velocidad de la muñeca en la imagen (torsos/s) o en world (m/s). "image" acierta
    # mejor el impacto en el banco de pruebas (scripts/bench_segmentation.py: 41/48 frente a 34/48).
    segmentation_signal: Literal["world", "image"] = "image"
    pose_skeleton_correction: bool = True  # inversiones izquierda/derecha y picos (pose/skeleton_correction.py)
    pose_smoothing_seconds: float = Field(default=0.1, ge=0, le=0.5)  # Savitzky-Golay; 0 = sin suavizado
    impact_from_audio: bool = True  # fija el impacto con el sonido del golpe cuando se oye
    metric_sensitivity_check: bool = True  # recalcula cada métrica moviendo el impacto dentro de su incertidumbre
    impact_uncertainty_seconds: float = Field(default=0.05, gt=0, le=0.2)  # impacto estimado por la muñeca
    # Comparación informativa con golpes de referencia (DTW). Sin referencias registradas en el bucket
    # (scripts/register_reference.py) el reporte dice "no_references" y nada más cambia.
    reference_comparison_enabled: bool = True
    reference_similarity_scale_deg: float = Field(default=20.0, gt=0)
    reference_refresh_seconds: float = Field(default=300.0, gt=0)
    log_level: str = "INFO"

    # Clasificación del golpe. Por defecto, comportamiento de la etapa 1: lo declara el usuario.
    stroke_classifiers: str = "user"  # en orden, separados por comas: user, pose_seq, yolo
    stroke_combiner: Literal["first", "cascade", "vote", "verify"] = "first"
    stroke_min_confidence: float = Field(default=0.7, gt=0, le=1)
    stroke_vote_weights: str = ""  # ej. "pose_seq=2,yolo=1" (por defecto, peso 1)
    stroke_models_dir: Path = Path("/tmp/tennis-models")  # caché local de modelos
    pose_seq_model_version: str = ""  # vacío = modelo montado en <stroke_models_dir>/pose_seq
    yolo_model_version: str = ""

    otel_exporter_otlp_endpoint: str | None = None  # sin valor, la telemetría queda desactivada
    otel_service_name: str = "tennis-worker"

    @field_validator("pose_model_path", "knowledge_base_dir")
    @classmethod
    def _must_exist(cls, path: Path) -> Path:
        if not path.exists():
            raise ValueError(f"no existe: {path}")
        return path

    @field_validator("stroke_classifiers")
    @classmethod
    def _known_classifiers(cls, value: str) -> str:
        ids = [part.strip() for part in value.split(",") if part.strip()]
        unknown = set(ids) - KNOWN_CLASSIFIERS
        if not ids or unknown:
            raise ValueError(f"clasificadores desconocidos {sorted(unknown)}; usa {sorted(KNOWN_CLASSIFIERS)}")
        return ",".join(ids)

    @field_validator("stroke_vote_weights")
    @classmethod
    def _parse_weights(cls, value: str) -> str:
        _weights(value)  # falla al arrancar si el formato es inválido
        return value

    @model_validator(mode="after")
    def _motionbert_needs_a_model(self) -> Settings:
        if self.pose_estimator == "rtmpose_motionbert" and not self.motionbert_model_version:
            raise ValueError("POSE_ESTIMATOR=rtmpose_motionbert requiere MOTIONBERT_MODEL_VERSION")
        return self

    @property
    def classifier_ids(self) -> list[str]:
        return self.stroke_classifiers.split(",")

    @property
    def vote_weights(self) -> dict[str, float]:
        return _weights(self.stroke_vote_weights)

    @property
    def dlq_stream(self) -> str:
        return self.job_dlq_stream or f"{self.job_stream}:dlq"

    @property
    def s3_endpoint_url(self) -> str:
        scheme = "https" if self.s3_use_ssl else "http"
        return f"{scheme}://{self.s3_endpoint}"
