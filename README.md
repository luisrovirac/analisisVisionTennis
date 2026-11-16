# Análisis de golpes de tenis por video

El usuario sube un video de su golpe y recibe una lista de errores **ordenados del más simple al
más complejo**, cada uno con su **corrección** y ejercicios. La cadena completa funciona de punta a punta, con
usuarios autenticados, trazas distribuidas, una cola que se recupera sola y un clasificador de golpes
configurable con su pipeline de entrenamiento versionado. Cada equipo (biomecánica, ML, móvil)
mejora su módulo detrás de una interfaz estable.

```
Cliente (web · Android KMP) ── login OIDC (Keycloak en local) ──► access token (JWT)
   │ 1. POST /v1/analyses ──► API Go ──► Postgres (awaiting_upload)
   │ 2. PUT video ──────────────────────► MinIO / R2 (URL prefirmada)
   │ 3. POST /v1/analyses/{id}/submit ─► API Go ──► Redis Stream
   │                                                 │
   │                                   Worker Python ◄┘
   │                                   video → pose (MediaPipe) → clasificación → segmentación
   │                                   → métricas → reglas YAML → priorización
   │                                   → Postgres (report) + MinIO (poses/{id}.json)
   │ 4. GET /v1/analyses/{id} ◄──────── API Go
```

## Arranque rápido

Requisitos: Docker Desktop.

```bash
cp .env.example .env                                   # ajusta las contraseñas
docker compose -f deploy/docker-compose.yml up --build -d
curl http://localhost:8080/readyz                      # {"checks":{"postgres":"ok","redis":"ok","storage":"ok"}}
```

Abre <http://localhost:8080>, inicia sesión con `alice` / `alice-dev-only` (o `bob` / `bob-dev-only`),
sube un forehand grabado de perfil y espera el resultado.

| Servicio | URL |
| --- | --- |
| Cliente web y API | <http://localhost:8080> |
| Keycloak (IdP de desarrollo; admin en `KC_BOOTSTRAP_ADMIN_*`) | <http://localhost:8180> |
| Grafana: trazas, métricas, logs y alertas (admin/admin) | <http://localhost:3001> |
| Consola de MinIO | <http://localhost:9001> |
| MLflow (`--profile ml`) | <http://localhost:5001> |

Prueba de punta a punta desde la terminal:

```bash
python scripts/e2e.py samples/forehand.mp4    # obtiene tokens de alice y bob y comprueba el aislamiento
```

## Estructura

| Carpeta | Contenido |
| --- | --- |
| `contracts/` | JSON Schema de los mensajes entre módulos (fuente de verdad) |
| `api/` | API en Go: casos de uso, Postgres, URLs prefirmadas, cola, cliente web embebido |
| `worker/` | Worker en Python: pipeline de visión y motor de reglas |
| `worker/knowledge_base/` | Errores y correcciones por golpe, en YAML editable por entrenadores |
| `deploy/` | docker-compose para desarrollo local, realm de Keycloak, dashboards y alertas de Grafana, servidor de MLflow |
| `ml/` | Pipeline de entrenamiento del clasificador (DVC + MLflow), en Docker |
| `mobile/` | App Kotlin Multiplatform: `shared` (red, auth, flujo) + `androidApp` (Compose). Ver [mobile/README.md](mobile/README.md) |
| `scripts/` | Utilidades (prueba de punta a punta) |
| `Docs/` | [Arquitectura](Docs/ARQUITECTURA.md), [contratos](Docs/CONTRATOS.md), [base de conocimiento](Docs/BASE_CONOCIMIENTO.md) |

## Desarrollo

**API (Go 1.26)**

```bash
cd api
go test ./... -cover
go vet ./...
# Tests contra Postgres real (con docker-compose levantado):
TEST_DATABASE_URL="postgres://tennis:<pass>@localhost:5432/tennis?sslmode=disable" go test -tags integration ./...
```

**Worker (Python 3.10–3.12)**

```bash
cd worker
python -m venv .venv && .venv/Scripts/activate        # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest --cov                                            # tests unitarios (sin modelo ni red)
ruff check src tests && mypy src
```

En Windows, onnxruntime ≥ 1.22 necesita un runtime de Visual C++ reciente (14.40+). Si `import
onnxruntime` falla con *DLL load failed*, instala el *Visual C++ Redistributable* actual o, solo en
local, `pip install onnxruntime==1.20.1`. En Docker y en CI (Linux) no pasa.

Tests de integración con MediaPipe real (opcional):

```bash
mkdir -p models samples
curl -L -o models/pose_landmarker_full.task \
  https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task
# copia un forehand real a samples/forehand.mp4
cd worker && pytest -m integration -s                  # -s imprime el reporte para calibrar umbrales
```

`models/` y `samples/` no se versionan.

**Pipeline de ML** (en Docker: torch, onnx y MLflow no dependen del sistema anfitrión)

```bash
docker compose -f deploy/docker-compose.yml --profile ml up -d mlflow
docker compose -f deploy/docker-compose.yml --profile ml run --rm ml python -m tennis_ml make-toy   # datos sintéticos
docker compose -f deploy/docker-compose.yml --profile ml run --rm ml dvc repro export_pose_seq     # con dataset.name: toy
docker compose -f deploy/docker-compose.yml --profile ml run --rm ml pytest -q                     # test de punta a punta
```

Para el modelo real: copia THETIS RGB en `ml/data/raw/thetis`, ejecuta `dvc add data/raw/thetis` y
`dvc repro`. Después, `python -m tennis_ml publish export/pose_seq`. Detalles en [Arquitectura](Docs/ARQUITECTURA.md#mlops-ml).

**CI:** `.github/workflows/ci.yml` ejecuta la API (con Postgres real), el worker (cobertura ≥ 80 %),
el pipeline de ML con datos sintéticos y la app móvil (tests de `shared` + APK).

## Estado y limitaciones conocidas

- Solo el **forehand** tiene métricas y reglas. Los demás golpes devuelven `UNSUPPORTED_STROKE`,
  aunque el clasificador ya los distingue.
- Los **umbrales del YAML son provisionales** (`validated_by: null`). Hay que calibrarlos con videos
  reales y que los valide un entrenador.
- Un golpe por video. El impacto se estima como el pico de velocidad de la muñeca.
- **Clasificador:** el código, la configuración y el pipeline están listos, pero todavía **no hay un
  modelo publicado**: falta entrenarlo con THETIS. Mientras tanto el worker funciona como en la
  etapa 1 (`STROKE_CLASSIFIERS=user`).
- **App móvil:** Android listo. iOS comparte `shared`, pero falta su UI y su login (necesita macOS).
- Fuera de alcance por ahora: velocidad de la raqueta (requiere detectarla).
