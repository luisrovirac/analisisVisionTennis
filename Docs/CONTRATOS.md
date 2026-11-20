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
| `DELETE` | `/v1/analyses/{id}` | `204`. Borra el análisis, el video (`videos/{id}`) y la pose (`poses/{id}.json`). Responde `409 ANALYSIS_BUSY` si está `queued` o `processing` |
| `GET` | `/v1/me/preferences` | `200` `{handedness}`: preferencias del usuario (por defecto `right`). Los clientes la leen al iniciar sesión |
| `PUT` | `/v1/me/preferences` | `200` `{handedness}`. Reemplaza las preferencias (idempotente); `handedness` ∈ `right`, `left` |
| `PATCH` | `/v1/analyses/{id}` | `200` análisis. Cuerpo `{"expert_review_allowed": bool}`: el dueño cambia el permiso de revisión experta |
| `GET` | `/v1/me` | `200` `{id, email, email_verified, roles, status, created_at, profile}`. Los clientes deciden la navegación con `roles` |
| `PUT` | `/v1/me/profile` | `200` perfil profesional `{display_name, specialty, certifications, bio}` (solo expertos y validadores) |
| `GET` | `/auth/config` | `200` `{enabled, issuer, client_id}` (público: lo que necesita el cliente para iniciar sesión) |
| `GET` | `/healthz` · `/readyz` | Vida / dependencias (públicos) |

Cuerpo de creación: `{"stroke_type": "forehand", "handedness": "right", "camera_view": "side",
"expert_review_allowed": true}`. Todos los campos son opcionales. `expert_review_allowed` es el checkbox
«Permitir que expertos vean este video» (ausente = `true`): solo sirve para que los expertos comprueben
y calibren las reglas; el análisis del usuario siempre es automático. Sin `stroke_type`, el worker detecta el golpe con el clasificador
configurado, y el análisis muestra `stroke_type: null` hasta que se completa. `handedness` y
`camera_view` valen por defecto `right` / `side`.

`next_cursor` es opaco. Para pedir la página siguiente, pásalo tal cual; si es `null`, no hay más páginas.

Errores: `{"error": {"code": "...", "message": "..."}}`. El `message` está pensado para mostrarse
al usuario final.

| Código HTTP | `code` |
| --- | --- |
| 400 | `INVALID_INPUT` |
| 401 | `UNAUTHENTICATED` (token ausente, expirado, manipulado o de otra audiencia; incluye `WWW-Authenticate: Bearer`) |
| 403 | `FORBIDDEN` (falta el rol, o la acción no está permitida: p. ej. validar una propuesta propia) · `ACCOUNT_SUSPENDED` |
| 404 | `NOT_FOUND` |
| 409 | `CONFLICT` (el estado cambió entretanto: p. ej. la base de conocimiento activa ya no es sobre la que se escribió la propuesta) |
| 409 | `UPLOAD_MISSING` (submit antes de subir el video) |
| 413 | `UPLOAD_TOO_LARGE` |
| 415 | `UNSUPPORTED_MEDIA` |

Códigos de fallo del análisis (`analysis.error.code`): `VIDEO_UNREADABLE`, `VIDEO_TOO_LONG`,
`NO_PERSON_DETECTED`, `LOW_POSE_QUALITY`, `NO_STROKE_FOUND`, `UNSUPPORTED_STROKE` (golpe sin reglas, o
no reconocido con confianza suficiente; el mensaje distingue los dos casos), `MAX_ATTEMPTS_EXCEEDED`.

## Roles y portal profesional

Todo usuario autenticado es **usuario** (sube videos y ve sus análisis). Los roles extra se acumulan y
viven en Postgres (`user_roles`), no en el IdP: **admin**, **expert** y **validator** (una persona puede
ser experto y validador, pero nunca valida lo suyo). El admin invita por email; los roles se conceden al
iniciar sesión con ese email **verificado** (`email_verified` del token). `BOOTSTRAP_ADMIN_EMAILS` crea el
primer admin. Los roles los comprueba siempre la API; la UI solo oculta opciones.

| Método | Ruta | Rol | Qué hace |
| --- | --- | --- | --- |
| `GET` | `/v1/kb/metrics?stroke=` | expert · validator · admin | Catálogo de métricas que el worker sabe medir |
| `GET` | `/v1/kb/strokes/{stroke}` | expert · validator · admin | Versión activa con su `knowledge_base` |
| `GET` | `/v1/kb/releases?stroke=` | expert · validator · admin | Historial de versiones |
| `GET` | `/v1/kb/proposals?status=&mine=true` | expert · validator · admin | Propuestas |
| `GET` | `/v1/kb/proposals/{id}` | expert · validator · admin | Detalle: comentarios, versión activa, `impacts` (antes/después sobre análisis reales), `problems`, `stale` |
| `POST` | `/v1/kb/proposals/{id}/comments` | expert · validator · admin | Conversación experto ↔ validador |
| `POST` | `/v1/kb/preview` | expert · validator · admin | `{stroke, rule}` → en cuántos análisis recientes se dispararía |
| `GET` | `/v1/kb/metric-requests` | expert · validator · admin | Métricas pedidas por los expertos |
| `POST` | `/v1/kb/releases/{id}/activate` | validator · admin | Rollback a una versión válida anterior |
| `POST` · `PUT` | `/v1/expert/proposals[/{id}]` | expert | Crear / editar (solo el autor, en `draft` o `changes_requested`) |
| `POST` | `/v1/expert/proposals/{id}/submit` · `/withdraw` | expert | Enviar a validación (se ancla a la versión activa) / retirar |
| `POST` | `/v1/expert/metric-requests` | expert | Pedir una métrica nueva |
| `POST` | `/v1/validator/proposals/{id}/approve` · `/reject` · `/request-changes` | validator | `{note}` (obligatoria salvo al aprobar). Aprobar aplica los cambios y publica una versión nueva en una transacción |
| `GET` · `PUT` | `/v1/admin/users[/{id}/roles · /{id}/status]` | admin | Usuarios, roles y suspensión (un admin no puede quitarse su rol ni suspenderse) |
| `GET` · `POST` · `DELETE` | `/v1/admin/invitations[/{id}]` | admin | Invitaciones (14 días de validez) |
| `GET` | `/v1/admin/audit` | admin | Auditoría |

Una propuesta es `{stroke, title, rationale, evidence, changes}` con `changes` = lista de
`{"op": "upsert_rule", "rule": {...}}` o `{"op": "remove_rule", "rule_id": "..."}`. Las reglas siguen
`contracts/knowledge_base.schema.json`; los cuerpos JSON rechazan campos desconocidos y `validated` lo
fija la API al aprobar (nunca el experto).

## Reporte 1.1 (aditivo)

Campos opcionales; `schema_version` sigue en `1.0` y un reporte antiguo sigue siendo válido
(fixtures `contracts/fixtures/report.v1_0.example.json` y `report.v1_1.example.json`, validados desde
Python y Kotlin):

| Campo | Contenido |
| --- | --- |
| `strengths[]` | Puntos fuertes (máx. 5): `rule_id, rank, category, title, why_it_matters, how_to_maintain, evidence, confidence, validated` |
| `diagnosis` | `summary`, `root_cause_rule_id` (causa raíz según `caused_by`), `contributing_factors`, `strengths_to_leverage`, `limitations`, `unmeasurable` |
| `drill_plan` | Plan de 4 semanas, un foco a la vez: `weeks`, `reanalyze_after_weeks`, `items[{week, focus_rule_id, focus, drills}]` |
| `reliability` | `score` (detección × confianza media), `level` (`high`/`medium`/`low`; con avisos nunca `high`), `reasons` |
| `knowledge_status` | `status` (`provisional`/`partially_validated`/`validated`), `validated_rules`, `total_rules`. Sin nombres |
| `findings[].validated` | La regla que generó el hallazgo fue aprobada por un validador |

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
| `report` 1.1 (sin cambiar `schema_version`) | `strengths`, `diagnosis`, `drill_plan`, `reliability`, `knowledge_status` y `findings[].validated`, todos opcionales. |
| `job_message` 1.1 | `stroke_type` pasa a ser opcional (detección automática). El worker acepta 1.0 y 1.1. Hay que desplegar el worker antes que la API. |
