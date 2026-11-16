"""Configuración del worker: toda viene del entorno y se valida al arrancar (fail fast)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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
    knowledge_base_dir: Path

    max_video_seconds: float = Field(default=30.0, gt=0)
    max_attempts: int = Field(default=3, ge=1)
    job_claim_idle_seconds: int = Field(default=600, ge=10)
    min_pose_detection_rate: float = Field(default=0.6, gt=0, le=1)
    min_metric_confidence: float = Field(default=0.5, ge=0, le=1)
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
