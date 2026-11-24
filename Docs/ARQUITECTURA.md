# Arquitectura

## Decisiones

| Decisión | Motivo |
| --- | --- |
| API en Go, worker en Python | Go para la API y la orquestación. Python por el ecosistema de visión (MediaPipe, PyTorch). |
| El video va directo al almacenamiento (URL prefirmada) | La API nunca transporta archivos grandes: escala y es barata. |
| MinIO en local, Cloudflare R2 en producción | Ambos hablan S3. Cambiar de uno a otro es cambiar `S3_ENDPOINT`/`S3_PUBLIC_ENDPOINT` y las credenciales. |
| Redis Streams como cola | Grupo de consumidores, confirmación explícita (`XACK`) y recuperación de trabajos huérfanos (`XAUTOCLAIM`). Simple de operar. |
| PostgreSQL como estado | Una fila por análisis. El `report` se guarda como JSONB. La API es dueña del esquema (`api/migrations`). |
| Contratos JSON versionados | `contracts/` es la frontera entre equipos. Ver [CONTRATOS.md](CONTRATOS.md). |
| Reglas en YAML | Los entrenadores editan conocimiento sin tocar código. Ver [BASE_CONOCIMIENTO.md](BASE_CONOCIMIENTO.md). |
| OIDC + JWKS, la API como *resource server* | No hay auth propia: cualquier IdP OIDC sirve (Keycloak en local). La identidad estable es `(issuer, sub)` → `users.id`. |
| OpenTelemetry → otel-lgtm | Una traza cruza Go → Redis → Python. Un único contenedor (Collector, Tempo, Prometheus, Loki y Grafana) en desarrollo. |
| Clasificador configurable | Varias estrategias intercambiables por entorno (`STROKE_CLASSIFIERS` / `STROKE_COMBINER`). |
| DVC + MLflow, el worker sin MLflow | El worker carga una versión fija de `models/<clf>/<versión>/` del bucket: el arranque no depende del servidor de experimentos. |

## Ciclo de vida de un análisis

```
awaiting_upload ──submit──► queued ──worker──► processing ──► completed
                                                   │
                                                   └────────► failed (error_code + mensaje para el usuario)
```

- `submit` es **idempotente**. La transición `awaiting_upload → queued` es un `UPDATE` condicional,
  así que dos envíos simultáneos encolan una sola vez. Si falla la publicación en Redis, el estado
  vuelve a `awaiting_upload`.
- **Errores del video** (sin jugador, golpe no encontrado, video ilegible…) → `failed` sin
  reintento, con un mensaje accionable.
- **Errores de infraestructura** (DB/Redis/S3 caídos) → el mensaje no se confirma. Otro worker lo
  recupera tras `JOB_CLAIM_IDLE_SECONDS`. Cada intento incrementa `attempts`; por encima de
  `MAX_ATTEMPTS` el análisis pasa a `failed` y el mensaje va a la **DLQ** (`analysis_jobs:dlq`).
- **Mensajes ilegibles** (no cumplen el contrato) → DLQ directamente, con `reason=INVALID_PAYLOAD`.
- **Errores inesperados** (bugs) → no tumban el worker. El mensaje no se confirma y sigue el camino de los reintentos.
- **Entregas duplicadas** → `start_processing` solo avanza desde `queued`/`processing`. Si el análisis
  ya está resuelto, se confirma sin reprocesar.

La DLQ se inspecciona con `XRANGE analysis_jobs:dlq - +` (campos `payload`, `reason`, `original_id`,
`failed_at`, `traceparent`). Cada entrada guarda su `traceparent`, así que se puede abrir la traza
del intento que falló.

## Pipeline del worker

`worker/src/tennis_worker/pipeline/orchestrator.py` compone pasos inyectados. Cada paso es una
interfaz (`Protocol`) con su implementación actual:

| Paso | Interfaz | Implementación actual | Dónde mejorar |
| --- | --- | --- | --- |
| Lectura de video | `VideoReader` | `OpenCVVideoReader` (≤720p, streaming) | — |
| Pose | `PoseEstimator` | `MediaPipePoseEstimator` (33 puntos + coordenadas world) | Equipo ML: otros modelos |
| Corrección del esqueleto | `SkeletonCorrector` | Deshace inversiones izquierda/derecha del cuerpo entero (Viterbi) y picos de un fotograma, antes de interpolar y suavizar (`POSE_SKELETON_CORRECTION`) | Ancla de raqueta para tramos largos invertidos |
| Calidad | `QualityAssessor` | Tasa de detección, fps, vista | — |
| Clasificación | `StrokeRecognizer` = `StrokeClassifier`s + `ClassifierCombiner` | `user`, `pose_seq` (GRU/ONNX), `yolo` (ONNX) | Equipo ML (`ml/`) |
| Segmentación | `StrokeSegmenter` | `PeakStrokeSegmenter`: todos los golpes del video (picos de velocidad de la muñeca respecto a la cadera; saque por altura de la muñeca); el informe usa el de mayor pico. Ver [BANCO_SEGMENTACION.md](BANCO_SEGMENTACION.md) | Informe por sesión (varios golpes) |
| Métricas | `Metric` + `MetricRegistry` | 7 métricas de forehand | **Biomecánica** |
| Reglas | `RuleEngine` + YAML | Umbrales provisionales | **Entrenadores** |
| Priorización | `Prioritizer` | Fundamental → sincronización; impacto ↓, dificultad ↑ | Entrenadores |

Para añadir una métrica: crea una clase con `id`, `label`, `unit` y `compute(ctx)` en
`biomechanics/metrics.py` y regístrala en `biomechanics/registry.py`. El worker se niega a arrancar
si una regla del YAML cita una métrica que no existe.

El worker guarda también `poses/<id>.json` en el bucket. Así el equipo de biomecánica puede iterar
métricas offline sobre poses reales sin volver a procesar video.

## Clasificación del golpe

Se configura sin tocar código:

| Variable | Valores | Por defecto |
| --- | --- | --- |
| `STROKE_CLASSIFIERS` | lista ordenada: `user`, `pose_seq`, `yolo` | `user` |
| `STROKE_COMBINER` | `first`, `cascade`, `vote`, `verify` | `first` |
| `STROKE_MIN_CONFIDENCE` | umbral en (0, 1] | `0.7` |
| `STROKE_VOTE_WEIGHTS` | `pose_seq=2,yolo=1` (solo `vote`) | peso 1 |
| `POSE_SEQ_MODEL_VERSION` · `YOLO_MODEL_VERSION` | versión publicada en `models/<clf>/<versión>/` del bucket | vacío = modelo montado en `STROKE_MODELS_DIR/<clf>` |

- `first`: decide el primero que no se abstiene. `user` se abstiene si el usuario no declaró el golpe.
- `cascade`: decide el primero que supere el umbral. Los siguientes no se ejecutan, así que no se paga su inferencia.
- `vote`: media ponderada de las probabilidades de todos los clasificadores.
- `verify`: si el usuario declaró el golpe, se usa ese golpe y los modelos solo pueden añadir un aviso
  cuando discrepan. Si no lo declaró, funciona como `cascade` entre los modelos.

Si ningún clasificador da un golpe soportado con confianza suficiente (o el modelo dice `other`),
el análisis falla con `UNSUPPORTED_STROKE` y un mensaje accionable. El worker comprueba al arrancar
que cada modelo existe, que su salida coincide con sus clases y, para `pose_seq`, que se entrenó con la
misma versión de características (`FEATURE_SET`). Si algo falla, no arranca.

Configuración recomendada en cuanto haya un modelo publicado: `STROKE_CLASSIFIERS=user,pose_seq`,
`STROKE_COMBINER=verify`. Respeta lo que elige el usuario y detecta el golpe cuando no lo indica.

## MLOps (`ml/`)

```
data/raw/<dataset> ─prepare─► labels.csv ─extract_poses─► poses/*.json ─features─► features.npz
    (DVC)            (partición por jugador)   (MediaPipe del worker)    (features.py del worker)
features.npz ─train_pose_seq─► model.pt ─export_pose_seq─► export/pose_seq/{model.onnx, meta.json}
                 (MLflow: params, curvas)      (test, umbral calibrado, paridad ONNX = PyTorch, Model Registry)
labels + poses ─yolo_dataset─► frames + cajas del esqueleto ─train_yolo─► export/yolo/ (evaluado por clip con el worker)
```

- **Una sola implementación:** la extracción de pose y las características se importan del worker,
  así que entrenamiento e inferencia no pueden divergir. `export` además carga el ONNX con el
  clasificador del worker y comprueba que da las mismas probabilidades que PyTorch.
- **Trazabilidad:** `meta.json` lleva `mlflow_run_id` y `dataset_hash`, que es el md5 que DVC guarda
  en `dvc.lock` para `features.npz`. El reporte de cada análisis incluye ambos.
- **Despliegue:** `python -m tennis_ml publish export/pose_seq` sube el modelo a
  `models/pose_seq/<versión>/` y le pone el alias `production` en MLflow. Después se cambia
  `POSE_SEQ_MODEL_VERSION` en el worker y se reinicia.
- Todo se ejecuta en Docker (`docker compose --profile ml run --rm ml ...`). Así no depende del
  runtime de Visual C++ de Windows que exigen torch, onnx y onnxruntime.

Datasets: THETIS RGB es la base del clasificador. El dataset propio (`own`, con `labels.csv`) sirve
para validar en condiciones reales y para calibrar los umbrales del YAML con un entrenador.
CalTennis es CC-BY-NC: solo sirve para evaluar la pose, no para entrenar modelos de un producto
comercial. Ultralytics (ruta YOLO) es AGPL-3.0 y los modelos que entrena heredan esa licencia salvo
licencia Enterprise. Es otra razón para que `pose_seq` sea la ruta por defecto.

## Observabilidad

- **Trazas:** `otelgin` (un span por petición HTTP, sin las sondas de salud), `otelpgx` (SQL) y
  `redisotel` en la API. En el worker hay un span `analysis.process` (hijo del `POST /submit`,
  gracias al `traceparent` del stream) y un span por etapa.
- **Métricas:** `queue.pending` (XPENDING) y `queue.dlq.length` (XLEN) las emite la API, así siguen
  midiendo aunque no quede ningún worker vivo. El worker emite `analysis.stage.duration` (histograma
  por etapa) y `analysis.outcome` (por estado y código de error).
- **Logs:** JSON con `trace_id`/`span_id` en Go y en Python.
- **Grafana** (`http://localhost:3001`): dashboard *Tennis Vision* y dos alertas: trabajos sin
  confirmar durante 15 minutos y mensajes en la DLQ.

## Próximos pasos sugeridos

1. Descargar THETIS en `ml/data/raw/thetis`, ejecutar `dvc repro export_pose_seq` y publicar el
   primer modelo cuando alcance macro-F1 ≥ 0,90 en test.
2. Grabar 50–200 golpes reales (dataset `own`) para validar fuera de THETIS y calibrar el YAML con un entrenador.
3. App iOS sobre `mobile/shared` (necesita macOS).
4. Métricas y reglas de revés, saque y volea: el clasificador ya los reconoce, pero falta su base de conocimiento.
