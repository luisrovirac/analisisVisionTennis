# Contratos entre módulos

Los JSON Schema de `contracts/` son la **fuente de verdad**. Las implementaciones que los siguen son:

| Contrato | Productor | Consumidor | Implementaciones |
| --- | --- | --- | --- |
| `job_message.schema.json` (1.1) | API (Go) | Worker | `api/internal/queue/redis.go` · `worker/.../contracts.py::JobMessage` |
| `report.schema.json` (1.0) | Worker | API → clientes | `worker/.../contracts.py::Report` (la API lo devuelve tal cual) · `mobile/shared/.../model/Models.kt` |
| `pose_sequence.schema.json` (1.0) | Worker | Visor de esqueleto (web, portal, Android) · Biomecánica y ML (offline) | `worker/.../pose/serialization.py` (`to_artifact` / `from_artifact`) · `api/web/static/skeleton-viewer.js` · `mobile/shared/.../model/Models.kt` (`PoseSequence`) |
| `reference_stroke.schema.json` (1.0) | `scripts/register_reference.py` | Worker (comparación DTW) | `worker/.../pipeline/reference/serialization.py` · `worker/.../infrastructure/reference_store.py` |

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
| `GET` | `/v1/analyses/{id}/media` | `200` `{video_url, pose_url, expires_at}`: URLs firmadas de solo lectura (vigencia `UPLOAD_URL_TTL`) para el visor de esqueleto. `409 NOT_READY` si el análisis no está `completed` |
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
| 409 | `NOT_READY` (`/media` de un análisis que aún no está `completed`) |
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
| `GET` | `/v1/kb/cases?stroke=&rule_id=&limit=&cursor=` | expert · validator · admin | Casos: análisis `completed` con `expert_review_allowed`. `{items: [{id, stroke_type, camera_view, created_at, finding_rule_ids, strength_rule_ids}], next_cursor}`. `rule_id` filtra los casos en que esa regla disparó (error o punto fuerte). **Nunca incluye al dueño** |
| `GET` | `/v1/kb/cases/{id}` | expert · validator · admin | Un caso: `report`, `handedness`, `camera_view` y `video_url`/`pose_url` firmadas. `404` si el dueño retiró el permiso |
| `GET` | `/v1/kb/corpus?stroke=` | expert · validator · admin | Corpus de validación: `{items: [{analysis_id, stroke, split, note, added_at, ground_truth_rules}]}`. Solo casos cuyo dueño mantiene el permiso |
| `GET` | `/v1/kb/campaigns?stroke=` · `/v1/kb/campaigns/{id}` | expert · validator · admin | Campañas Delphi. El detalle trae `progress` (con nombres solo para el coordinador), `rounds[{round, rules[{rule_id, alpha, band, units, consensus_rate, not_evaluable}]}]` (la ronda en curso solo para quien no es evaluador) y `my_done` |
| `GET` | `/v1/kb/cases/{id}/reviews` | expert · validator · admin | Mi revisión libre (`mine`) y el agregado anónimo de las demás por regla |
| `POST` | `/v1/kb/calibrate` | expert · validator · admin | `{stroke, rule, split: calibration\|test, sweep?: {condition, from, to, steps}}` → matriz de confusión, precisión/recall (con IC de Wilson), F1 contra el ground truth, y `curve` si hay barrido |
| `GET` | `/v1/kb/quality?stroke=` | expert · validator · admin | KPIs de la base: estados de validación, α, acierto en test, corpus, reglas a auditar y por qué |
| `PUT` | `/v1/kb/cases/{id}/review` | expert · validator | Revisión libre: `{player_level, notes, items: [{rule_id, rating (1-7) \| not_evaluable, verdict, comment}]}`. `system_fired` se congela desde el reporte. `403` sobre los videos propios |
| `GET` · `PUT` | `/v1/kb/campaigns/{id}/cases/{caseId}[/review]` | expert · validator (evaluadores) | Vista **ciega** del caso (video, pose, fases, aspectos a calificar, feedback Delphi de la ronda anterior) y calificación sin veredicto. `403 NOT_A_RATER`, `409 CAMPAIGN_CLOSED` |
| `POST` · `DELETE` | `/v1/kb/corpus[/{id}]` | validator · admin | Agregar `{analysis_id, note}` (split determinista ≈70/30) / quitar un caso del corpus |
| `GET` | `/v1/kb/professionals` | validator · admin | Expertos y validadores activos (posibles evaluadores) |
| `POST` | `/v1/kb/campaigns` | validator · admin | `{stroke, title, rule_ids, case_ids, rater_ids, max_rounds, consensus_pct, min_raters}` (casos del corpus; por defecto 3 rondas, 70 %, 3 evaluadores) |
| `POST` | `/v1/kb/campaigns/{id}/advance` · `/close` | validator · admin | `{force}` abre la siguiente ronda (sin `force` exige todas las calificaciones). Cerrar fija el ground truth (`case_ground_truth`) en una transacción |
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
`contracts/knowledge_base.schema.json`; los cuerpos JSON rechazan campos desconocidos. `validated`,
`validation_status` y `validation` los fija la API al aprobar (nunca el experto); el experto solo puede
pedir `validation_status: empirically_validated`, que exige evidencia contra el ground truth de test
(`422 VALIDATION_EVIDENCE_INSUFFICIENT` si no alcanza). Cada versión publicada (`/v1/kb/releases`) trae
`changelog_entries[{rule_id, action, fields, thresholds[{metric, before, after}], bump}]`, `bump`
(`major`/`minor`/`patch`), `proposed_by_name` y `validated_by_name`. El proceso completo está en
`Docs/BASE_CONOCIMIENTO.md` («Validación formal de las reglas»).

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

## Reporte 1.2 (aditivo)

Igual que la 1.1: `schema_version` sigue en `1.0` y los campos son opcionales.

| Campo | Contenido |
| --- | --- |
| `metrics[].landmarks` | Índices MediaPipe (0-32) de los landmarks en los que se apoya la métrica. El visor los resalta al pulsar «Ver en el video» en un hallazgo (unión de los de su `evidence`) |
| `reference_comparison` | Comparación **informativa** con golpes de referencia (DTW sobre ángulos articulares, ver `Docs/BASE_CONOCIMIENTO.md`): `status` (`ok`/`partial`/`low_confidence`/`no_references`), `references_used[{ref_id, label}]`, `overall_similarity` (0-100), `phases[{phase, status, similarity, confidence}]` con `phase` ∈ `preparation`/`acceleration`/`follow_through` y `status` ∈ `ok`/`low_confidence`/`too_short`, y `note`. Sin puntaje (`null`) cuando no hay referencias o el jugador no se vio con seguridad. No genera errores ni puntos fuertes |

La pose (`poses/{id}.json`) añade, también de forma opcional, `width` y `height` de los fotogramas
analizados: el visor compara esa relación de aspecto con la del video para detectar rotaciones mal
aplicadas y, si no coinciden, muestra solo el esqueleto.

**CORS del almacenamiento:** el visor descarga la pose con `fetch()` desde la URL firmada, así que el bucket
debe permitir `GET` desde el origen de la web (MinIO lo permite por defecto; en R2 hay que añadir `GET` a
la política CORS del bucket junto al `PUT` de la subida). El `<video>` no lo necesita.

## Reporte 1.3 (aditivo)

| Campo | Contenido |
| --- | --- |
| `findings[].validation_status` · `strengths[].validation_status` | Nivel de validación de la regla (KB 2.1): `heuristic_unvalidated`, `expert_reviewed` o `empirically_validated`. Sin nombres de expertos |

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
| `report` 1.2 (sin cambiar `schema_version`) | `metrics[].landmarks` y `reference_comparison`, opcionales. La pose añade `width`/`height` opcionales. |
| `job_message` 1.1 | `stroke_type` pasa a ser opcional (detección automática). El worker acepta 1.0 y 1.1. Hay que desplegar el worker antes que la API. |
| `report` 1.3 (sin cambiar `schema_version`) | `findings[].validation_status` y `strengths[].validation_status`, opcionales. |
| `knowledge_base` 2.1 (aditivo) | `rules[].validation_status`, `validation`, `evidence` y `evidence_level`, opcionales (`validated` se mantiene como legado = estado ≠ `heuristic_unvalidated`). Desplegar la API y el worker juntos: un worker antiguo rechaza los campos nuevos. Migración `0007_expert_validation`. |
| `knowledge_base` 2.0 (aditivo) | `rules[].views` opcional (`side`, `front`, `back`): vistas de cámara en las que la regla es fiable. Hay que desplegar la API y el worker juntos: una API antigua rechaza el campo y un worker antiguo no lo carga. |
