"""Registra un golpe de referencia para la comparación DTW (informativa) del worker.

El video pasa por el mismo pipeline de pose que un análisis real (relleno de huecos, suavizado, segmentación,
impacto por audio), se comprueba que la pose sea fiable y se sube al bucket:

    references/<golpe>/<ref_id>.json   (contracts/reference_stroke.schema.json)
    references/index.json              (índice que el worker relee cada REFERENCE_REFRESH_SECONDS)

Uso (desde la raíz del repo, con el entorno del worker y la infraestructura de docker-compose levantada):

    worker/.venv/Scripts/python.exe scripts/register_reference.py samples/drive_perfil.mp4 \
        --stroke forehand --view side --handedness right --ref-id club-fh-side-1 \
        --label "Drive de perfil (entrenador del club)" --source "Grabación propia, cesión firmada 2026-11"

Las credenciales salen del entorno o de .env (S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET); el endpoint por
defecto es S3_PUBLIC_ENDPOINT (el que se alcanza desde fuera de docker). --dry-run analiza sin subir.

Usa solo videos con permiso de uso: el campo --source deja constancia de la procedencia.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker" / "src"))

from tennis_worker.pipeline.audio import AudioImpactRefiner, PyAvAudioReader  # noqa: E402
from tennis_worker.pipeline.orchestrator import SizeRecordingStream, prepare_pose  # noqa: E402
from tennis_worker.pipeline.pose.mediapipe_estimator import MediaPipePoseEstimator  # noqa: E402
from tennis_worker.pipeline.pose.skeleton_correction import SkeletonCorrector  # noqa: E402
from tennis_worker.pipeline.reference import ReferenceStroke  # noqa: E402
from tennis_worker.pipeline.reference.acceptance import (  # noqa: E402
    DEFAULT_DETECTION_SCOPE,
    DETECTION_SCOPES,
    AcceptanceCriteria,
    assess_reference,
)
from tennis_worker.pipeline.reference.serialization import (  # noqa: E402
    INDEX_KEY,
    index_entry,
    reference_key,
    to_reference_artifact,
    upsert_index,
)
from tennis_worker.pipeline.segmentation import PeakStrokeSegmenter  # noqa: E402
from tennis_worker.pipeline.video import OpenCVVideoReader  # noqa: E402

REF_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def load_dotenv(path: Path) -> None:
    """Rellena os.environ con .env sin pisar lo ya definido (sin dependencias extra)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_reference(args: argparse.Namespace) -> ReferenceStroke:
    with OpenCVVideoReader(max_seconds=60).open(args.video) as stream:
        sized = SizeRecordingStream(stream)
        raw = replace(MediaPipePoseEstimator(args.mediapipe_model).estimate(sized), frame_size=sized.size)
    pose = prepare_pose(raw, None if args.no_skeleton_correction else SkeletonCorrector(), args.smoothing)
    segmenter = PeakStrokeSegmenter()
    if args.impact_frame is not None:
        if not 0 <= args.impact_frame < pose.frame_count:
            raise SystemExit(f"--impact-frame {args.impact_frame} fuera del clip (0-{pose.frame_count - 1})")
        seg, impact_source = segmenter.segment_at(pose, args.handedness, args.impact_frame), "fotograma manual"
    else:
        refined = AudioImpactRefiner(PyAvAudioReader(), rebuild=segmenter.segment_at).refine(
            segmenter.segment(pose, args.handedness, args.stroke), pose, args.video, args.handedness)
        seg, impact_source = refined.segment, refined.source
    criteria: AcceptanceCriteria = args.criteria
    assessment = assess_reference(pose, seg, criteria)
    print(f"Detección del jugador: {assessment.detection_rate:.0%} en el ámbito «{criteria.detection_scope}» "
          f"({pose.detection_rate:.0%} en todo el clip) · impacto por {impact_source} · "
          f"fotogramas {seg.start_frame}-{seg.end_frame} (impacto {seg.impact_frame})")
    for name, visibility in assessment.phase_visibility.items():
        print(f"  fase {name:15s} visibilidad media {visibility:.2f}")
    if assessment.problems and not args.force:
        raise SystemExit("La grabación no es lo bastante fiable para servir de referencia: "
                         + "; ".join(assessment.problems)
                         + ". Graba de nuevo siguiendo el protocolo o usa --force bajo tu responsabilidad.")
    return ReferenceStroke(ref_id=args.ref_id, label=args.label, stroke=args.stroke, camera_view=args.view,
                           handedness=args.handedness, source=args.source, pose=pose, segment=seg)


def upload(ref: ReferenceStroke, args: argparse.Namespace) -> None:
    import boto3
    from botocore.config import Config as BotoConfig

    from tennis_worker.infrastructure.s3_store import S3ObjectStore

    endpoint = args.endpoint or os.environ.get("S3_PUBLIC_ENDPOINT") or os.environ["S3_ENDPOINT"]
    use_ssl = (os.environ.get("S3_PUBLIC_USE_SSL") or os.environ.get("S3_USE_SSL", "false")).lower() == "true"
    client = boto3.client(
        "s3", endpoint_url=f"{'https' if use_ssl else 'http'}://{endpoint}",
        region_name=os.environ.get("S3_REGION", "us-east-1"),
        aws_access_key_id=os.environ["S3_ACCESS_KEY"], aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        config=BotoConfig(signature_version="s3v4"),
    )
    store = S3ObjectStore(client, os.environ["S3_BUCKET"])
    store.put_json(reference_key(ref.stroke, ref.ref_id), to_reference_artifact(ref))
    registered_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.put_json(INDEX_KEY, upsert_index(store.get_json(INDEX_KEY), index_entry(ref, registered_at)))
    print(f"Referencia subida a {reference_key(ref.stroke, ref.ref_id)} e índice actualizado. "
          "El worker la usará en el próximo refresco del índice.")


def main() -> None:
    load_dotenv(ROOT / ".env")  # antes de los argumentos: REFERENCE_* puede venir de .env
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path)
    parser.add_argument("--stroke", required=True, choices=["forehand", "backhand", "serve", "volley"])
    parser.add_argument("--view", required=True, choices=["side", "front", "back"])
    parser.add_argument("--handedness", default="right", choices=["right", "left"])
    parser.add_argument("--ref-id", required=True, help="identificador corto: minúsculas, números, - y _")
    parser.add_argument("--label", required=True, help="nombre visible para el jugador")
    parser.add_argument("--source", required=True, help="procedencia y permiso de uso del video")
    parser.add_argument("--mediapipe-model", type=Path, default=ROOT / "models" / "pose_landmarker_full.task")
    parser.add_argument("--smoothing", type=float, default=0.1)
    parser.add_argument("--no-skeleton-correction", action="store_true",
                        help="no deshacer inversiones izquierda/derecha ni picos (POSE_SKELETON_CORRECTION=false)")
    parser.add_argument("--endpoint", default="", help="host:puerto S3 (por defecto S3_PUBLIC_ENDPOINT)")
    parser.add_argument("--dry-run", action="store_true", help="analiza y valida, sin subir nada")
    parser.add_argument("--force", action="store_true", help="registra aunque la pose no sea fiable")
    parser.add_argument("--impact-frame", type=int, default=None,
                        help="fotograma del impacto verificado a ojo (0 = primero del clip); sustituye a la "
                             "detección por muñeca y audio, útil con audio ruidoso (pistas vecinas)")
    defaults = AcceptanceCriteria()
    parser.add_argument("--detection-scope", choices=sorted(DETECTION_SCOPES),
                        default=os.environ.get("REFERENCE_DETECTION_SCOPE", DEFAULT_DETECTION_SCOPE),
                        help="fotogramas sobre los que se mide la detección: 'stroke' = solo el golpe "
                             "(por defecto), 'clip' = todo el video (env REFERENCE_DETECTION_SCOPE)")
    parser.add_argument("--min-detection-rate", type=float,
                        default=float(os.environ.get("REFERENCE_MIN_DETECTION_RATE", defaults.min_detection_rate)),
                        help="detección mínima en el ámbito elegido, 0-1 (env REFERENCE_MIN_DETECTION_RATE)")
    parser.add_argument("--min-phase-visibility", type=float,
                        default=float(os.environ.get("REFERENCE_MIN_PHASE_VISIBILITY",
                                                     defaults.min_phase_visibility)),
                        help="visibilidad media mínima por fase, 0-1 (env REFERENCE_MIN_PHASE_VISIBILITY)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # consolas de Windows
    if not REF_ID.match(args.ref_id):
        parser.error("--ref-id: usa minúsculas, números, '-' y '_' (máx. 64)")
    if not args.video.exists():
        parser.error(f"no existe {args.video}")
    try:
        args.criteria = AcceptanceCriteria(min_detection_rate=args.min_detection_rate,
                                           min_phase_visibility=args.min_phase_visibility,
                                           detection_scope=args.detection_scope)
    except ValueError as exc:
        parser.error(str(exc))
    ref = build_reference(args)
    if args.dry_run:
        print("--dry-run: no se sube nada.")
        return
    upload(ref, args)


if __name__ == "__main__":
    main()
