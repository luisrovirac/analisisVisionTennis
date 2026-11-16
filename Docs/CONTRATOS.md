# Contratos entre módulos

Los JSON Schema de `contracts/` son la **fuente de verdad**. Las implementaciones que los siguen son:

| Contrato | Productor | Consumidor | Implementaciones |
| --- | --- | --- | --- |
| `job_message.schema.json` (1.1) | API (Go) | Worker | `api/internal/queue/redis.go` · `worker/.../contracts.py::JobMessage` |
| `report.schema.json` (1.0) | Worker | API → clientes | `worker/.../contracts.py::Report` (la API lo devuelve tal cual) · `mobile/shared/.../model/Models.kt` |
| `pose_sequence.schema.json` (1.0) | Worker | Biomecánica y ML (offline) | `worker/.../pose/serialization.py` (`to_artifact` / `from_artifact`) |

Cómo se comprueban:

- **Mensaje de cola:** `contracts/fixtures/job_message*.example.json` se verifican desde los dos lados.
  El test de Go comprueba que la API produce exactamente esos mensajes. El de Python
  (`worker/tests/test_contracts.py`) comprueba que cumplen el schema y que el worker los acepta.
- **Reporte y pose:** los tests del worker los validan contra el schema (`test_orchestrator.py`, `test_pose.py`).

La traza distribuida (`traceparent`, W3C) viaja en el stream como un campo aparte de `payload`. No
forma parte del contrato: un worker que no la conoce la ignora.

## API HTTP v1

Todas las rutas `/v1` exigen `Authorization: Bearer <access token>`, un JWT de un proveedor OIDC. La
API verifica la firma (JWKS), el emisor (`iss`), la audiencia (`aud`) y la expiración antes de llegar
al handler. Cada usuario solo ve sus análisis: un análisis ajeno responde `404`, igual que uno que no
existe, para no revelar que existe.

| Método | Ruta | Respuesta |
| --- | --- | --- |
| `POST` | `/v1/analyses` | `201` `{analysis, upload_url, expires_at}` |
| `POST` | `/v1/analyses/{id}/submit` | `202` análisis (idempotente) |
| `GET` | `/v1/analyses/{id}` | `200` análisis con `report` cuando está `completed` |
| `GET` | `/v1/analyses?limit=20&cursor=...` | `200` `{items: [resumen sin report], next_cursor}` (del más reciente al más antiguo) |
| `GET` | `/auth/config` | `200` `{enabled, issuer, client_id}` (público: lo que necesita el cliente para iniciar sesión) |
| `GET` | `/healthz` · `/readyz` | Vida / dependencias (públicos) |

Cuerpo de creación: `{"stroke_type": "forehand", "handedness": "right", "camera_view": "side"}`.
Los tres campos son opcionales. Sin `stroke_type`, el worker detecta el golpe con el clasificador
configurado, y el análisis muestra `stroke_type: null` hasta que se completa. `handedness` y
`camera_view` valen por defecto `right` / `side`.

`next_cursor` es opaco. Para pedir la página siguiente, pásalo tal cual; si es `null`, no hay más páginas.

Errores: `{"error": {"code": "...", "message": "..."}}`. El `message` está pensado para mostrarse
al usuario final.

| Código HTTP | `code` |
| --- | --- |
| 400 | `INVALID_INPUT` |
| 401 | `UNAUTHENTICATED` (token ausente, expirado, manipulado o de otra audiencia; incluye `WWW-Authenticate: Bearer`) |
| 404 | `NOT_FOUND` |
| 409 | `UPLOAD_MISSING` (submit antes de subir el video) |
| 413 | `UPLOAD_TOO_LARGE` |
| 415 | `UNSUPPORTED_MEDIA` |

Códigos de fallo del análisis (`analysis.error.code`): `VIDEO_UNREADABLE`, `VIDEO_TOO_LONG`,
`NO_PERSON_DETECTED`, `LOW_POSE_QUALITY`, `NO_STROKE_FOUND`, `UNSUPPORTED_STROKE` (golpe sin reglas, o
no reconocido con confianza suficiente; el mensaje distingue los dos casos), `MAX_ATTEMPTS_EXCEEDED`.

## Reporte: `classification`

Campo opcional del reporte (no cambia `schema_version`): indica cómo se decidió el golpe.

```json
"classification": {"stroke": "forehand", "confidence": 0.93, "classifier_id": "pose_seq",
                   "model_version": "20261115-3fa9c2d1", "dataset_hash": "md5:..."}
```

`classifier_id` puede ser `user` (lo declaró el usuario), `pose_seq`, `yolo` o `vote`. Con
`model_version` y `dataset_hash` se localizan en MLflow el experimento y en DVC los datos exactos que
produjeron el modelo. Si un modelo discrepa con lo que declaró el usuario (combinador `verify`),
el aviso aparece en `quality.warnings`.

## Versionado

Los cambios compatibles (campos nuevos opcionales) no cambian la versión: los consumidores ignoran
los campos desconocidos. Un cambio incompatible obliga a subir `schema_version` y a que el consumidor
soporte ambas versiones durante la migración.

| Versión | Cambio |
| --- | --- |
| `job_message` 1.1 | `stroke_type` pasa a ser opcional (detección automática). El worker acepta 1.0 y 1.1. Hay que desplegar el worker antes que la API. |
