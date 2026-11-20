# Plan de upgrade: análisis confiable + portal de expertos + 4 roles

## Contexto

Hoy el flujo funciona de punta a punta (subes un video → API Go → MinIO → Redis → worker Python con MediaPipe → 7 métricas de derecha → 8 reglas YAML → reporte JSONB → web y Android). Pero:

- **El análisis no es confiable todavía.** Hay un solo golpe (derecha), las métricas no se normalizan por cámara ni por cuerpo y los umbrales son provisionales (`worker/knowledge_base/forehand.yaml`, `validated_by: null`). El reporte solo lista errores: no hay puntos fuertes, ni causa raíz, ni plan de ejercicios.
- **No hay roles.** `api/internal/auth/verifier.go` solo lee `sub` y `email`, y `users` (`api/migrations/0002_users.up.sql`) no tiene rol.
- **El conocimiento está encerrado en código.** Cambiar un umbral exige editar el YAML y redesplegar el worker. Ningún especialista puede aportar sin un ingeniero de por medio.

Este plan toma lo mejor de los tres documentos (`upgrade18-11-2026de/op/cl.md`) y lo reordena alrededor de lo que pediste: **(1)** visión y análisis certeros, **(2)** un **entorno de expertos** donde especialistas de tenis y biomecánica, creados y administrados por el admin, enriquezcan reglas, referencias y recomendaciones, y **(3)** 4 tipos de usuario.

### Decisiones confirmadas
| # | Decisión |
|---|---|
| D1 | Los roles viven en **Postgres** (`user_roles`). El admin invita expertos y validadores por email, y el rol se asigna al iniciar sesión con ese email **verificado**. Keycloak sigue siendo solo el IdP. |
| D1b | **4 roles: `admin`, `user`, `expert`, `validator`.** El «usuario futuro» se elimina. Una misma persona puede ser experto y validador a la vez, pero **nunca valida sus propios aportes**. |
| D1c | **Flujo de aportes:** el experto propone → el validador aprueba o rechaza → **al aprobar, el cambio se hace efectivo en la BD** (genera automáticamente una nueva versión publicada de la KB). No hay un paso extra de «publicar». |
| D2 | **Dos carriles en paralelo**: Carril 1 (crítico) = confiabilidad + roles + portal. Carril 2 = endurecimiento de la plataforma (de `op.md`/`cl.md`), sin bloquear al carril 1. |
| D3 | **Checkbox por subida** «Permitir que expertos vean este video», **marcado por defecto** y desmarcable. Se guarda por análisis (`analyses.expert_review_allowed`). Los expertos ven esos análisis con su video, sin el email del usuario. No hay más protocolo. Además existe un corpus propio subido por admin o expertos. Para qué sirve: comprobar si el análisis acierta y calibrar los umbrales con casos reales. |
| D4 | Contrato del reporte: **aditivo 1.1** (campos opcionales; `schema_version` se queda en `1.0`), como recomienda `op.md` §6. |
| D5 | **La base de conocimiento pasa a Postgres** como fuente de verdad, con versiones publicadas inmutables. El YAML queda como semilla y como formato de exportación. |
| D6 | Las recomendaciones son de coaching, no médicas: hay descargo explícito y derivación a fisioterapeuta. |

Fuera de alcance: iOS, reescribir la web con un framework pesado y un LLM que decida (si se usa, solo redacta a partir del JSON).

---

## 1. Los 4 roles

| Capacidad | `admin` | `user` (todos) | `expert` | `validator` |
|---|:-:|:-:|:-:|:-:|
| Subir video, pedir y ver sus análisis | ✔ | ✔ | ✔ | ✔ |
| Permitir o no que expertos vean cada video (checkbox al subir) | ✔ | ✔ | ✔ | ✔ |
| Proponer aportes: reglas, umbrales, puntos fuertes, drills, referencias | ✖ | ✖ | ✔ | ✖ |
| Revisar y etiquetar casos (corpus + videos permitidos) | ✖ | ✖ | ✔ | ✔ |
| Aprobar o rechazar propuestas (el cambio se aplica a la BD) | ✖ | ✖ | ✖ | ✔ (nunca lo suyo) |
| Revertir una versión de la KB a la anterior | ✔ (auditado) | ✖ | ✖ | ✔ |
| Invitar, suspender y asignar roles expert/validator | ✔ | ✖ | ✖ | ✖ |
| Consola admin: auditoría, usuarios, uso, historial de versiones | ✔ | ✖ | ✖ | ✖ |

- **Modelo**: todo usuario autenticado es `user`. Los roles extra se acumulan en `user_roles(user_id, role)`, con `role ∈ {admin, expert, validator}`. Así una persona puede ser `expert` y `validator` a la vez.
- **Alta**: cualquiera que se registra es `user`. Expertos y validadores solo existen por invitación del admin (o porque el admin asciende a un usuario existente).
- **Primer admin**: la variable `BOOTSTRAP_ADMIN_EMAILS` (validada al arrancar) da rol admin al primer login con email verificado.
- **Perfil profesional** (experto y validador): especialidad (`tennis_coach | biomechanics | physio | fitness`), certificaciones y biografía. Queda como firma interna de cada cambio (portal y auditoría), nunca en el reporte del usuario.
- **Seguridad**: los roles se comprueban siempre en la API (middleware `requireRole`). La UI solo oculta opciones. La regla «nadie valida lo suyo» se aplica en el servicio, no en la UI. Cada cambio de rol y cada decisión de validación queda en `audit_log`.

---

## 2. Arquitectura de la capa de conocimiento (el corazón del portal)

```
Experto (portal) ──► API Go ──► kb_proposals (cambio propuesto: diff + motivo + evidencia)
                                   │  validación automática: esquema + catálogo de métricas
                                   ▼
Validador (portal) ── aprueba ──► en UNA transacción: aplica el cambio en kb_rules / kb_drills /
                   └ rechaza       kb_references + crea kb_releases (snapshot inmutable, semver,
                     (con motivo)   changelog, firmas proponente/validador)
                                   ▼
Worker ── PostgresKnowledgeBaseRepository.load(stroke) ── caché por release_id (revalida cada N s)
   └─ el reporte guarda knowledge_base_version = "forehand@1.3.0" → reproducible y auditable
```

Principios:
- **Nada se aplica sin validador.** Un experto nunca escribe directo en las tablas vivas: crea una propuesta (`draft → submitted → approved | rejected | changes_requested`). Solo la aprobación de un validador distinto del autor aplica el cambio. Cada aprobación crea una versión nueva e inmutable, y el worker la toma en segundos sin reiniciar. Rollback = reactivar la versión anterior (validador o admin).
- **Una propuesta puede agrupar varios cambios** (p. ej. una regla nueva y sus 2 drills), que se aprueban o rechazan juntos. Si dos propuestas tocan la misma regla, al aprobar la segunda se detecta el conflicto con `base_version` y se pide rebase.
- **Los expertos componen con métricas existentes; no crean métricas.** El worker publica su **catálogo de métricas** (id, unidad, golpe, rango físico y descripción) en una tabla `metric_catalog` al arrancar. La API valida cada regla contra ese catálogo. Si un experto necesita una métrica nueva, abre una «solicitud de métrica», que entra al backlog de ingeniería y se ve en el portal.
- **Doble validación.** La API valida contra `contracts/knowledge_base.schema.json` y el catálogo. El worker vuelve a validar al cargar (hoy lo hace `validate_knowledge` en `worker/src/tennis_worker/main.py`). Si falla, conserva la versión previa y marca la nueva como `invalid` con el motivo, visible en el portal.
- **Se reutiliza la interfaz existente** `KnowledgeBaseRepository.load(stroke)` (`worker/src/tennis_worker/pipeline/knowledge/repository.py`). Solo se añade una implementación Postgres. `YamlKnowledgeBaseRepository` queda para tests y desarrollo sin BD.

### Esquema de regla v2 (extiende `pipeline/knowledge/models.py`, compatible con el actual)
Campos nuevos, todos opcionales al principio:
- `polarity: error | strength`;
- `severity`;
- `normative_range_by_level` (beginner / intermediate / advanced);
- `caused_by[]` y `prerequisites[]` (grafo causal);
- `evidence_level: provisional | literature | expert_validated`;
- `references[]` (ids de `kb_references`, p. ej. los PMID de `op.md` §10.1);
- `why_it_matters` y `how_to_maintain` (para puntos fuertes);
- `drills[]` como **ids** de `kb_drills`;
- `contraindications[]`;
- `example_media[]` (videos bueno/malo).

---

## 3. Carril 1: confiabilidad + roles + portal (ruta crítica)

Las fases están ordenadas por dependencia, sin fechas.

### F0. Cimientos
1. Higiene: agregar `.env.bak-*` a `.gitignore` y commitear en bloques el trabajo pendiente (preferences, diagnostics de ml, mobile).
2. **Contrato 1.1 aditivo** en `contracts/report.schema.json`:
   - `strengths[]`;
   - `diagnosis` (summary, root_cause_rule_id, contributing_factors, limitations, unmeasurable, confidence);
   - `drill_plan`;
   - `reliability` (puntaje de confianza del reporte y motivos);
   - `knowledge_status` (provisional / validated, sin nombres).

   Lleva fixtures v1.0 y v1.1 validados desde Go, Python (`worker/src/tennis_worker/contracts.py`) y Kotlin (`mobile/shared/.../model/Models.kt`), con un test de «ignorar campos desconocidos».
3. `GET /v1/capabilities`: golpes soportados y autodetección on/off. Web y Android construyen la UI desde ahí. Se oculta «Detectar automáticamente» mientras `STROKE_CLASSIFIERS=user`.

### F1. Visión y análisis certeros (antes de que los expertos ajusten umbrales)
El objetivo es que cada número sea **medido de forma estable** y que el sistema **calle cuando no sabe**.
1. **Validación de ingesta**: `ffprobe` (tipo real, duración, fps, rotación) antes de encolar. Si el video es inválido, se rechaza con un código accionable.
2. **Normalización**: perfil canónico de fps, resolución y orientación. El esqueleto se centra en la cadera y se escala por la longitud del torso, así las métricas no dependen de la distancia a la cámara.
3. **Suavizado temporal** de landmarks (filtro One-Euro o Savitzky-Golay) y relleno corto de huecos, con confianza reducida en los frames interpolados.
4. **Verificación de vista de cámara** (perfil vs frontal) con nuevo código `CAMERA_VIEW_MISMATCH`. Las métricas sensibles a la vista bajan su confianza o se omiten.
5. **Confianza por métrica** basada en visibilidad, estabilidad e interpolación. Se conserva el filtro `min_metric_confidence` del motor (`pipeline/knowledge/engine.py`) y se extiende a los puntos fuertes.
6. **Honestidad epistémica** en el reporte: se separa lo medido, lo inferido y lo no observable con esta grabación (`diagnosis.limitations` / `unmeasurable`).
7. **Puntos fuertes con las 7 métricas actuales** (FH-STR-001…007 de `cl.md` §2.C / S1–S7 de `op.md` §B2.2), con reglas anti-inflación:
   - solo con confianza suficiente;
   - solo si superan claramente el rango del nivel;
   - entre 1 y 5 por reporte;
   - nunca si contradicen una regla de salud.

   El motor devuelve errores y fortalezas por separado; `prioritization.py` genera `strengths[]`.
8. **Golden set de regresión**: 20–30 videos con resultado esperado, ejecutados en CI. Si cambia el pipeline o la KB y se mueve una métrica o un hallazgo, el test lo detecta. Es la base para medir la precisión de forma objetiva.

### F2. Identidad y roles
1. Migración `0005_roles.up.sql`:
   - `user_roles(user_id, role)` con `role ∈ {admin, expert, validator}` (PK compuesta: permite tener varios roles); `users.status` (`active|suspended`);
   - `professional_profiles` (especialidad, certificaciones, biografía);
   - `invitations` (email, roles[], invitado_por, token de un solo uso, expira, aceptada);
   - `audit_log`.
2. `api/internal/auth/verifier.go`: leer también el claim `email_verified`. `domain.Principal` gana `Roles` y `UserResolver` (`api/internal/repository/users.go`) los carga. En el primer login se resuelven la invitación y el bootstrap de admin.
3. Middleware `requireRole(...)` en `api/internal/httpapi/router.go`. Se crean los grupos `/v1/expert/*` (requiere `expert`), `/v1/validator/*` (requiere `validator`) y `/v1/admin/*` (requiere `admin`). Un usuario `suspended` recibe `403 ACCOUNT_SUSPENDED` (nuevo error de dominio en `domain/errors.go` y su mapeo en `httpapi/errors.go`).
4. `GET /v1/me` devuelve roles y perfil, y la web decide qué navegación mostrar (pestañas «Mis análisis», «Portal experto», «Validación», «Admin»).
5. Permiso por video: columna `analyses.expert_review_allowed BOOLEAN NOT NULL DEFAULT true`. `POST /v1/analyses` acepta el campo (ausente = `true`). En web (`app.js`) y Android (`MainActivity.kt`) hay un checkbox «Permitir que expertos vean este video», marcado por defecto. El usuario puede cambiarlo después desde el detalle del análisis con `PATCH /v1/analyses/{id}`.
6. **Consola admin (MVP)**:
   - invitar a un experto y/o validador (genera un enlace; el email puede venir después);
   - listar usuarios, asignar o quitar los roles expert/validator/admin y suspender;
   - ver la auditoría.

   Keycloak sin cambios estructurales; solo se activa `verifyEmail` en `deploy/keycloak/realm-tennis.json`.

### F3. Portal de expertos + flujo de validación (MVP)
1. Migración `0006_knowledge.up.sql`:
   - `metric_catalog`;
   - `kb_rules`, `kb_drills`, `kb_references`: **tablas vivas**, que solo se escriben al aprobar;
   - `kb_proposals`: autor, golpe, `base_version`, lista de cambios (crear/editar/retirar regla, drill o referencia) como JSONB, motivo, evidencia, estado `draft|submitted|changes_requested|approved|rejected`, `decided_by`, `decided_at`, `decision_note`;
   - `kb_proposal_comments` (conversación experto ↔ validador);
   - `kb_releases`: snapshot JSONB por golpe, semver, changelog, `proposal_id`, `proposed_by`, `validated_by`, `active`;
   - `metric_requests`.

   Un script importa `forehand.yaml` como release `forehand@0.1.0` (provisional, sin validador).
2. Endpoints:
   - **Experto** `/v1/expert/*`:
     - `GET /metrics` (catálogo);
     - lectura de la KB viva (`GET /rules`, `/drills`, `/references`);
     - `POST|PUT /proposals` y `POST /proposals/{id}/submit`;
     - `POST /proposals/{id}/comments`;
     - `POST /metric-requests`.
   - **Validador** `/v1/validator/*`:
     - `GET /proposals?status=submitted` (bandeja);
     - `GET /proposals/{id}` (diff contra la versión viva + previsualización de impacto);
     - `POST /proposals/{id}/approve | reject | request-changes` (con nota obligatoria al rechazar o pedir cambios);
     - `POST /releases/{id}/activate` (rollback).
   - **Reglas de negocio** (en `knowledge_service.go`, con tests):
     - `approver ≠ author`;
     - la propuesta debe estar `submitted`;
     - validación automática de esquema y catálogo antes de habilitar «Aprobar»;
     - conflicto si `base_version` ya no es la activa;
     - la aprobación aplica los cambios y crea la release **en una sola transacción**;
     - todo queda en `audit_log`.
3. Worker:
   - `PostgresKnowledgeBaseRepository` carga la release `active` por golpe, con caché por `release_id`;
   - al arrancar escribe `metric_catalog` desde `biomechanics/registry.py`;
   - si una release no valida, la marca `invalid` y se queda con la anterior.
4. **UI** (`api/web/static/expert/` y `validator/`, módulos ES y render con `textContent`, sin framework):
   - *Experto*:
     - KB viva por golpe, con estado y nivel de evidencia;
     - editor de propuesta en formulario, no YAML crudo: elige métrica del catálogo, operador, umbrales por nivel, textos, drills y referencias;
     - **previsualización de impacto**: «con este umbral, la regla se dispara en X de N casos», calculada sobre las métricas ya guardadas de los análisis, sin reprocesar video;
     - «Mis propuestas» con estado y comentarios.
   - *Validador*:
     - bandeja de propuestas;
     - diff lado a lado;
     - impacto en casos reales y concordancia con las etiquetas de F4;
     - botones Aprobar / Pedir cambios / Rechazar;
     - historial de versiones con rollback.
5. **Los expertos NO analizan videos de usuarios ni hacen inferencias manuales.** El análisis de cada video es siempre 100 % automático. Los expertos mejoran las reglas, umbrales, puntos fuertes y drills del sistema, y una vez aprobado el cambio se aplica automáticamente a todos los análisis. El reporte del usuario no muestra nombres de expertos; la autoría (quién propuso y quién validó cada regla) queda solo en el portal interno y en la auditoría.

### F4. Enriquecimiento experto (el área que crece)
1. **Control de calidad de las reglas (no es un servicio al usuario)**: los videos sirven solo como muestra para comprobar si las reglas automáticas aciertan. El usuario no recibe nada de esta revisión, y su análisis no cambia. Cola de análisis (corpus + los que tienen `expert_review_allowed = true`, sin el email del usuario) con video, overlay del esqueleto (desde `poses/<id>.json`), métricas y hallazgos. El experto marca por regla ✔ acertó / ✖ falso positivo / ✖ faltó, etiqueta el nivel del jugador y agrega notas. Tabla `case_reviews`.
2. **Calibración medible**: un script calcula la concordancia experto–sistema por regla (precisión/recall; objetivo ≥ 0,8) a partir de `case_reviews`. Se muestra al validador en cada propuesta como apoyo a su decisión. No se puede marcar `evidence_level: expert_validated` sin una muestra mínima.
3. **Rangos normativos por nivel** y **grafo causal** (`caused_by`/`prerequisites`). El prioritizador ordena así: causa raíz primero, luego impacto, luego dificultad. El reporte muestra 1 causa raíz con sus síntomas.
4. **Biblioteca de drills como entidades**: objetivo, métricas, nivel, material, series y reps, criterio de éxito, progresión/regresión, foco atencional y video. Se genera un **plan de 4 semanas** con 1 foco por vez y re-análisis sugerido.
5. **Feedback del usuario**: `POST /v1/analyses/{id}/feedback` («¿te sirvió?», drills hechos). Lo ven los expertos.

### F5. Expansión de la visión
1. **Multi-golpe**: registrar métricas y KB de revés, saque (modelo de 8 fases) y volea en `biomechanics/registry.py`. Cada golpe entra con KB provisional; los expertos proponen y los validadores aprueban en el portal.
2. **Multi-repetición**: un segmentador de 3 a 10 golpes que reemplaza a `WristSpeedSegmenter`, con `segmentation_summary` aditivo y frecuencia por hallazgo («7 de 10»).
3. **Raqueta y pelota**: modelo con licencia permisiva (evitar AGPL), con las compuertas MLOps actuales de `Docs/pasosSiguientesEnDev.md`.
4. Clasificador automático: calibración por temperatura de `pose_seq`. Solo se activa si pasa las compuertas; mientras tanto se usa `verify` con la declaración del usuario.

---

## 4. Carril 2: plataforma (en paralelo, de `op.md` Plan A y `cl.md` Punto 1)
Prioridad dentro del carril:
1. **Seguridad**:
   - headers CSP/HSTS;
   - CORS explícito;
   - rate limit y cuota por usuario (Redis);
   - presigned POST con `content-length-range`;
   - tokens web fuera de localStorage;
   - MinIO con política mínima.
2. **Fiabilidad**:
   - outbox transaccional para `submit`;
   - CLI de DLQ;
   - presupuestos de tiempo por etapa;
   - limpieza de `awaiting_upload` > 24 h;
   - borrado verificado en BD y S3;
   - `DELETE /v1/me` y `GET /v1/me/export`.
3. **UX**:
   - progreso por SSE;
   - reproductor con overlay de esqueleto y fases (compartido con el portal);
   - sección «Lo que haces bien» primero, después foco y plan;
   - historial de progreso;
   - guía de grabación.
4. **Observabilidad**: SLOs, dashboards por etapa y por `error.code`, runbooks, auditoría.
5. **Infra/CI**:
   - imágenes multi-stage;
   - escaneo (govulncheck, pip-audit, Trivy, gitleaks);
   - migraciones como paso separado;
   - E2E en CI;
   - staging.
6. **Android**: paridad (fortalezas, diagnóstico, plan, checkbox de permiso) y push al completar. Los portales de experto, validador y admin son solo web.

---

## 5. Archivos clave
- **Contratos**: `contracts/report.schema.json` y nuevo `contracts/knowledge_base.schema.json`, con fixtures; `Docs/CONTRATOS.md`.
- **API**:
  - `internal/auth/verifier.go`;
  - `internal/domain/{errors,analysis}.go` y nuevos `domain/{role,knowledge}.go`;
  - `internal/httpapi/router.go` y nuevos `httpapi/{expert,admin}.go`;
  - `internal/repository/users.go` y nuevo `repository/knowledge.go`;
  - nuevos `internal/service/{knowledge,admin}_service.go`, siguiendo el patrón de `preferences_service.go`;
  - `migrations/0005_roles`, `0006_knowledge`, `0007_case_reviews_feedback`.
- **Worker**:
  - `pipeline/knowledge/{models,engine,repository}.py`;
  - `pipeline/prioritization.py`;
  - `pipeline/biomechanics/{metrics,registry}.py`;
  - normalización y suavizado en el paso de pose;
  - `main.py` (catálogo de métricas y repo Postgres);
  - `knowledge_base/forehand.yaml` (semilla).
- **Web**: `api/web/static/{app,auth}.js` se divide en módulos, más `expert/`, `validator/` y `admin/`.
- **Móvil**: `mobile/shared/.../model/Models.kt`, `TennisApi.kt`, `androidApp/.../MainActivity.kt`.
- **Deploy**: `deploy/keycloak/realm-tennis.json` (verifyEmail) y la variable `BOOTSTRAP_ADMIN_EMAILS`.

## 6. Verificación
- **Go**:
  - tests de `requireRole` (matriz rol × ruta: user, expert, validator, expert+validator, admin, suspended);
  - invitación + email verificado → roles asignados; con email no verificado no se asignan;
  - un experto+validador no puede aprobar su propia propuesta (403);
  - aprobar una propuesta con una métrica inexistente → rechazo;
  - conflicto por `base_version` desactualizada;
  - la aprobación aplica los cambios y crea la release de forma atómica (si algo falla, no queda nada a medias);
  - integración con Postgres en `postgres_integration_test.go`.
- **Worker**:
  - métricas normalizadas invariantes a escala y traslación (pose sintética);
  - una fortaleza no se dispara con baja confianza ni bajo el umbral, y se respeta el máximo de 5;
  - el grafo causal elige la raíz;
  - `PostgresKnowledgeBaseRepository` cambia de release sin reiniciar y conserva la anterior si la nueva no valida;
  - cobertura ≥ 80 %.
- **Contrato**: fixtures v1.0 y v1.1 validados desde Go, Python y Kotlin.
- **Golden set** en CI: métricas dentro de la tolerancia y hallazgos esperados.
- **E2E** (`scripts/e2e.py`):
  - un admin invita a un experto y a un validador;
  - el experto propone cambiar un umbral; el validador pide cambios, el experto ajusta y el validador aprueba;
  - un usuario sube un video y el reporte trae la nueva `knowledge_base_version`, `strengths.length ≥ 1` y estado de validación;
  - el validador hace rollback y el siguiente análisis vuelve a la versión anterior;
  - un análisis con el checkbox desmarcado no aparece en la cola del experto, y si el experto pide su video recibe 404.
- **Manual**: levantar `deploy/docker-compose.yml` y recorrer las 4 vistas (admin, usuario, experto, validador) en la web, y la de usuario en Android.

## 7. Riesgos principales
| Riesgo | Mitigación |
|---|---|
| Un cambio dañino llega a producción | Ningún experto escribe directo: solo un validador distinto aprueba. Releases inmutables, previsualización de impacto, rollback en un clic, auditoría |
| Cuello de botella de validadores | La bandeja muestra la antigüedad de cada propuesta; el admin puede nombrar más validadores; propuestas agrupadas |
| Escalada de privilegios por email | Rol solo con `email_verified=true` e invitación de un solo uso con expiración |
| Privacidad de videos de usuarios | Checkbox desmarcable en cada subida, sin email visible para el experto, URLs prefirmadas de vida corta. Si el usuario desmarca después, el análisis sale de la cola de inmediato |
| Elogios infundados | Reglas anti-inflación (F1.7), mismo filtro de confianza que los errores |
| «Validado» con muestra pequeña | `expert_validated` exige concordancia ≥ 0,8 con una muestra mínima |
| Lectura médica de las recomendaciones | Descargo, `contraindications`, derivación a fisioterapeuta |
