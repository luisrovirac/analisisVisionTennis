# Upgrade 18-11-2026 — Plan integral de mejora del producto

| Campo | Valor |
| --- | --- |
| Fecha del documento | 18-11-2026 |
| Estado | Propuesto, pendiente de ejecución |
| Horizonte | 12 meses |
| Audiencia | Ejecutiva, técnica y operativa (secciones diferenciadas) |
| Orden de ejecución | **Plan A completo → Plan B completo** (sin recortes parciales) |
| Fuera de alcance | iOS (móvil, UI SwiftUI, login `ASWebAuthenticationSession`, job macOS) |
| Equipo previsto | Multidisciplinar: ingeniería + entrenador/biomecánico + fisioterapia deportiva |
| Restricción de planificación | **Sin cronograma semanal ni estimación de esfuerzo**, por decisión expresa. Las olas se ordenan por dependencia, no por fecha. |

---

## 0. Resumen ejecutivo

El producto tiene hoy dos componentes que le dan valor:

1. **La plataforma**: frontend web + API Go + worker Python + PostgreSQL + Redis Streams + almacenamiento S3 + infraestructura + móvil Android.
2. **El conocimiento experto**: biomecánica, ciencia del tenis y experiencia de entrenadores, actualmente reducida a 8 reglas de forehand con umbrales provisionales.

La plataforma está **técnicamente bien construida pero incompleta en producto**: la cadena funciona de punta a punta, con autenticación, trazas, cola autocurativa y contratos versionados, pero el cliente es aún un visor de texto con *polling*, solo se analiza un golpe por video, no hay clasificador publicado y la operación en producción no está endurecida.

El conocimiento está **en estado semilla**: solo existe `forehand.yaml` (v0.1.0, `validated_by: null`), las métricas son 7 aproximaciones sin calibrar y el informe **solo contiene errores**. No se detecta ni se comunica **ningún punto fuerte**, no hay diagnóstico de causa raíz, no hay planes de ejercicios estructurados y no hay validación de experto.

Este documento propone:

- **Plan A**: cerrar y maximizar la plataforma (contratos, fiabilidad, rendimiento, seguridad, observabilidad, UX, infraestructura, Android).
- **Plan B**: llevar el conocimiento a otro nivel, empezando por **multi-golpe**, con detección explícita de puntos fuertes, conclusiones causales, recomendaciones basadas en aprendizaje motor y biblioteca de ejercicios validada, incluyendo raqueta/pelota en una ola intermedia y portal del entrenador.
- **Dos decisiones transversales**: el clasificador se gobierna por un **archivo de configuración con todos los caminos posibles**, y la evolución del contrato del reporte se documenta en sus **cuatro alternativas** con recomendación fundamentada.

La tesis central es que **ambos planes se apoyan en el mismo puente**: un `report` enriquecido (puntos fuertes + diagnóstico + plan de entrenamiento) y una base de conocimiento gobernada como código validado por expertos.

---

## 1. Alcance y decisiones confirmadas

### 1.1 Decisiones tomadas con el usuario

| # | Decisión | Consecuencia en este documento |
| --- | --- | --- |
| D1 | Documento para **todos los públicos** | §0 ejecutivo, §2–§3–§4 detalle técnico, §7–§9 operación y métricas |
| D2 | **Equipo multidisciplinar** | Se incluyen responsibilities de entrenador, biomecánico y fisioterapeuta, no solo tareas de código |
| D3 | **12 meses, sin iOS** | Plan A incluye Android; queda registrado el coste de oportunidad de iOS, sin trabajo |
| D4 | **Plan A completo y Plan B completo**, en ese orden | Secuencia secuencial de planes; se documentan las dependencias que se anticipan en A0 |
| D5 | Foco inicial del punto 2: **multi-golpe** | Plan B arranca por revés/saque/volea antes que por la profundización aislada de forehand |
| D6 | Clasificador gobernado por **archivo de configuración**, con todas las posibilidades abiertas | §5: matriz completa de clasificadores, combinadores, umbrales y perfiles |
| D7 | Contrato de reporte: **documentar las 4 alternativas** | §6: aditivo 1.1, ruptura 2.0, campos solo internos y decisión diferida, con recomendación |
| D8 | **Revisión bibliográfica con citas verificadas** | §10.1: 20 referencias verificadas vía PubMed (PMID/DOI); los umbrales numéricos siguen siendo provisionales |
| D9 | Raqueta y pelota **en ola intermedia** | Plan B, ola 2, después de multi-golpe y antes del cierre |
| D10 | **Sin cronograma semanal ni estimación de esfuerzo** | Las olas son bloques de dependencia; no hay fechas ni horas |
| D11 | **Portal del entrenador incluido** | Plan B, ola 4, con roles OIDC y flujo de aprobación de KB |
| D12 | **50–200 vídeos reales + entrenador** disponibles | Plan B, ola 1: calibración con corpus propio y firma experta |

### 1.2 Fuera de alcance explícito

- iOS: UI SwiftUI, login con `ASWebAuthenticationSession`, job en `macos-latest`, xcframework. Queda como deuda consciente, no como tarea pendiente de este plan.
- Reescritura del cliente web a un framework pesado. Se prefiere modularización incremental (véase A5).
- Investigación clínica con riesgo para pacientes: las recomendaciones son de coaching deportivo, no diagnóstico médico.

### 1.3 Principios rectores (heredados de `RULES.md`)

- **KISS / YAGNI**: no se añade arquitectura speculativa; cada cambio responde a una brecha inventariada.
- **DRY**: una sola fuente de verdad para contratos (`contracts/`), métricas (`registry.py`) y conocimiento (`knowledge_base/`).
- **OCP + Strategy/DIP**: nuevas métricas, clasificadores, operadores y reglas se registran, no se ramifican con `if/elif`.
- **Contrato primero**: `contracts/*.schema.json` es la frontera entre equipos; ningún cambio incompatible sin versión nueva.
- **Fail fast**: configuración validada al arrancar; el worker se niega a iniciar si una regla cita una métrica inexistente.
- **Pruebas de comportamiento observable**: cobertura ≥ 80 % en worker, contratos verificados desde los dos lados, E2E con aislamiento de usuarios.
- **Honestidad epistémica**: separar medido, inferido y no observable; no presentar umbrales provisionales como validados.

---

## 2. Estado actual del sistema (inventario técnico)

Esta sección es la base factual sobre la que se construyen ambos planes. Todo lo aquí descrito existe en el repositorio a la fecha del documento.

### 2.1 Arquitectura desplegada

```
Cliente web (embebido en la API) · Android KMP
        │ login OIDC (Keycloak en local)
        ▼
API Go (cmd/, internal/{auth,config,domain,httpapi,queue,repository,service,storage,telemetry})
        │ 1. POST /v1/analyses ─────────► Postgres (awaiting_upload)
        │ 2. PUT video (URL prefirmada) ► MinIO / R2
        │ 3. POST /v1/analyses/{id}/submit ► Redis Stream
        │                                      │
        │                          Worker Python (pasos inyectados)
        │                          video → pose → calidad → clasificación → segmentación
        │                               → métricas → reglas YAML → priorización
        │                               → Postgres (report JSONB) + S3 (poses/<id>.json)
        │ 4. GET /v1/analyses/{id} ◄──────── API
```

Decisiones ya tomadas y que se respetan:

- La API nunca transporta el video (URL prefirmada directa a S3).
- MinIO en local, Cloudflare R2 en producción; mismo protocolo S3.
- Redis Streams con grupo de consumidores, `XACK` explícito y `XAUTOCLAIM`.
- PostgreSQL guarda el `report` como JSONB; la API es dueña del esquema (`api/migrations`).
- OIDC + JWKS; identidad estable `(issuer, sub)` → `users.id`.
- OpenTelemetry → otel-lgtm (Tempo, Prometheus, Loki, Grafana) en desarrollo.
- El worker **no consulta MLflow en runtime**: descarga una versión fija desde el bucket al arrancar.

### 2.2 Pipeline del worker

| Paso | Interfaz | Implementación actual | Punto de mejora |
| --- | --- | --- | --- |
| Lectura | `VideoReader` | `OpenCVVideoReader` (≤720p, streaming) | Normalización y validación previa |
| Pose | `PoseEstimator` | `MediaPipePoseEstimator` (33 puntos + coordenadas world) | Batching, GPU, warm pool |
| Calidad | `QualityAssessor` | Tasa de detección, fps, vista | Bloqueo previo en cliente |
| Clasificación | `StrokeRecognizer` | `user`, `pose_seq`, `yolo` + `first/cascade/vote/verify` | Sin modelo publicado |
| Segmentación | `StrokeSegmenter` | `WristSpeedSegmenter` (pico de muñeca) | Un golpe por video |
| Métricas | `Metric` + `MetricRegistry` | 7 métricas de forehand | Solo forehand |
| Reglas | `RuleEngine` + YAML | 6 operadores, umbrales provisionales | Sin reglas positivas ni causalidad |
| Priorización | `Prioritizer` | Categoría → impacto ↓ → dificultad ↑ | Sin causa raíz ni frecuencia |

Ciclo de vida: `awaiting_upload → queued → processing → completed`, con `failed` y DLQ (`analysis_jobs:dlq`) para errores de infraestructura y mensajes ilegibles.

### 2.3 Base de conocimiento y métricas

`worker/knowledge_base/forehand.yaml` (v0.1.0, `validated_by: null`) contiene 8 reglas: 5 fundamentales y 3 de sincronización. Todas son **errores**; ninguna describe un acierto.

Las 7 métricas registradas en `biomechanics/registry.py` son:

| id | Qué mide | Unidad |
| --- | --- | --- |
| `shoulder_rotation_range` | Rango de giro de hombros, preparación → impacto | grados |
| `hip_shoulder_separation_max` | Separación máxima cadera-hombros antes del impacto | grados |
| `elbow_angle_at_impact` | Ángulo de codo dominante en el impacto (180 = recto) | grados |
| `knee_angle_min` | Ángulo mínimo de rodilla en preparación (180 = recta) | grados |
| `contact_point_ahead` | Distancia de la muñeca por delante de la cadera en el impacto | metros |
| `follow_through_height` | Altura máxima de muñeca sobre el hombro tras el impacto | metros |
| `kinetic_chain_sequence` | 1 si cadera → hombros → brazo; 0 si no | 0/1 |

El motor (`knowledge/engine.py`) filtra métricas con `confidence < min_metric_confidence` y calla en lugar de arriesgar una corrección. Ese criterio se conserva y se extiende a los puntos fuertes.

### 2.4 Contratos y clientes

- `report.schema.json` v1.0: `schema_version`, `stroke_type`, `segment`, `metrics[]`, `findings[]`, `quality`, `knowledge_base_version`, `classification?`.
- `findings[]` es una lista de errores con `rule_id`, `rank`, `category`, `title`, `explanation`, `correction`, `drills[]` y `evidence`.
- API HTTP v1: 10 rutas documentadas, paginación por cursor, borrado en cascada, preferencias de mano dominante.
- Web: 228 líneas de `app.js`, JS vanilla, *polling* cada 2 s, render con `textContent` (seguro frente a XSS).
- Móvil: `shared` (red, auth, modelo) + `androidApp` (Compose); iOS declarado pero sin UI.

### 2.5 Brechas que este plan cierra

| Brecha | Plan y sección |
| --- | --- |
| Contrato no cubre positivos ni diagnóstico | A0 / §6, B2–B4 |
| `submit` con ventana de inconsistencia DB↔Redis | A1 |
| Videos no normalizados antes de medir | A2 |
| Sin rate limiting, cuotas ni secretos gestionados | A3 |
| Sin SLOs, runbooks ni métricas de negocio | A4 |
| UI sin visor de esqueleto ni progreso por etapa | A5 |
| Infraestructura sin IaC ni despliegues canary | A6–A8 |
| Solo forehand, un golpe por video | B1, B5 |
| Solo errores, sin puntos fuertes | B2 |
| Sin causa raíz ni pronóstico | B3 |
| Drills como cadenas de texto | B4 |
| Sin raqueta ni pelota | B5 (ola intermedia) |
| KB sin gobernanza ni portal | B0, B6 |
| Umbrales sin calibrar | B7 |

---

# PLAN A — PLATAFORMA (se ejecuta completo antes del Plan B)

## A0. Cimientos y contratos

**Objetivo**: dejar la frontera entre módulos lista para recibir los campos nuevos del Plan B sin romper a los clientes actuales.

1. **Evolución del contrato `report` a 1.1 de forma aditiva**. Campos nuevos opcionales: `strengths[]`, `diagnosis`, `drill_plan`, `trend`, `segmentation_summary`. La decisión completa, con las 4 alternativas, está en §6.
2. **Pruebas de compatibilidad hacia atrás** en los tres consumidores: Go (API), Python (worker) y Kotlin (`mobile/shared`). Un fixture v1.0 debe seguir siendo aceptado tras el cambio.
3. **Outbox transaccional para `submit`**. Hoy el flujo es `UPDATE` condicional `awaiting_upload → queued` + publicación en Redis con reversión si falla. Sustituir por tabla `analysis_outbox` escrita en la misma transacción que el cambio de estado y un publicador idempotente con reintentos. Elimina la ventana en la que el análisis está `queued` pero el mensaje nunca llegó.
4. **CLI de DLQ** (`scripts/dlq.py` o subcomando): listar, inspeccionar con `traceparent` y reprocesar mensajes con `reason`/`original_id`. Sustituye el `XRANGE analysis_jobs:dlq - +` manual.
5. **ADRs** (`Docs/adr/`) para cada decisión nueva y un `Taskfile.yml` único con `dev`, `test`, `verify`, `e2e`, `migrate`.
6. **Anticipación de dependencias del Plan B** sin implementarlas aún: dejar huecos de esquema para artifacts de overlay (`poses/<id>.json` ya existe) y para el almacenamiento de feedback de drills.

**Criterio de salida**: contrato 1.1 validado por Go, Python y Kotlin en CI; cero estados `queued` huérfanos en pruebas de fallo de Redis; DLQ operable desde CLI; ADRs de contrato y outbox aprobados.

## A1. Confiabilidad y correctitud

1. **Taxonomía de errores ampliada**. Códigos actuales: `VIDEO_UNREADABLE`, `VIDEO_TOO_LONG`, `NO_PERSON_DETECTED`, `LOW_POSE_QUALITY`, `NO_STROKE_FOUND`, `UNSUPPORTED_STROKE`, `MAX_ATTEMPTS_EXCEEDED`. Añadir: `RACKET_NOT_VISIBLE`, `BALL_NOT_VISIBLE`, `MULTIPLE_STROKES`, `CAMERA_VIEW_MISMATCH`, `METRIC_CONFIDENCE_LOW`. Cada uno con mensaje accionable para el usuario y acción sugerida en la UI.
2. **Presupuestos de tiempo por etapa** (lectura, pose, segmentación, métricas, reglas). Un video problemático no debe ocupar un worker indefinidamente; el exceso se convierte en `failed` con código específico, no en reintento infinito.
3. **Política de reintentos diferenciada**: errores de video → sin reintento; errores de infraestructura → reintento con backoff hasta `MAX_ATTEMPTS` y DLQ; errores inesperados → sin `XACK` y recuperación por `XAUTOCLAIM`.
4. **Pruebas de caos en staging**: matar un worker a mitad de etapa, caer Redis, caer S3, caer Postgres. Verificar que el trabajo se recupera o llega a DLQ con traza intacta.
5. **Mutation testing** sobre `knowledge/engine.py` y `prioritization.py`: mutar `<` por `<=`, `impact` por `-impact`, el orden de categorías. Si los tests no mueren, la cobertura es engañosa.
6. **Aislamiento multiusuario automatizado**: ampliar el E2E `alice`/`bob` (ya existente) a listado paginado, borrado, cuotas y subida concurrente.
7. **Idempotencia de extremo a extremo**: dos `submit` simultáneos, dos entregas del mismo mensaje y dos reintentos deben producir un único reporte y un único artifact de pose.

**Criterio de salida**: toda falla inyectada termina en `completed` o en `failed` con código correcto y traza consultable; ningún trabajo se pierde ni se procesa dos veces; mutantes supervivientes = 0 en reglas y priorización.

## A2. Rendimiento y escalabilidad

### A2.1 Ingesta

- **Validación antes de encolar**: `ffprobe` para tipo real, duración, resolución, fps y rotación. Rechazar con `415 UNSUPPORTED_MEDIA` o `413 UPLOAD_TOO_LARGE` antes de consumir worker.
- **Normalización del video** a un perfil canónico (fps, resolución, orientación, códec). Las métricas asumen vista de perfil y 30/60 fps; normalizar reduce falsos diagnósticos y hace comparables los análisis del mismo usuario.
- **Subida resumible / multipart** para videos grandes, con URL prefirmada por parte y reanudación tras pérdida de red.
- **Límite de duración y resolución por plan**, leído de configuración, no escrito en código.

### A2.2 Worker

- **Warm pool**: mantener processes con el modelo de pose cargado para evitar el arranque frío de MediaPipe/ONNX.
- **Inferencia por lotes** de fotogramas y delegado GPU cuando esté disponible; caída automática a CPU con aviso en `quality.warnings`.
- **Concurrencia por etapa** y *backpressure* según `queue.pending` (métrica ya emitida por la API).
- **Autoescalado del pool** de workers, con reserva separada para trabajos GPU cuando se publique `pose_seq`.
- **Caché de modelos** en `stroke_models_dir` con verificación de integridad y versión fija (comportamiento actual, conservado).

### A2.3 Datos

- **Índice GIN** sobre `analyses.report` JSONB para consultas por `stroke_type`, `error.code` y versión de KB.
- **Índices de cobertura** para la paginación por cursor (`created_at`, `id`).
- **Particionado por `created_at`** y **réplica de lectura** para `GET /v1/analyses`.
- **Lifecycle de storage**: video → cold storage → borrado; TTL de `poses/<id>.json`; presupuesto por análisis.
- **Caché de JWKS** y de reportes leídos con frecuencia.

**Criterio de salida**: p95 por etapa medido y estable bajo carga; vídeos inválidos rechazados antes del worker; costo de almacenamiento por análisis decreciente; análisis repetido produce el mismo reporte.

## A3. Seguridad y privacidad

1. **Gestión de secretos**: pasar de `.env` a gestor administrado (Vault / AWS Secrets Manager) en producción; rotación de credenciales S3 y claves de firma. El `.env` actual queda restringido a desarrollo.
2. **Rate limiting y cuotas**: por `users.id` en `POST /v1/analyses` y en la subida; límite de número de análisis, duración y resolución por plan. Respuesta `429` con mensaje accionable.
3. **Cifrado en reposo** (SSE) de videos y poses; URLs prefirmadas de vida corta; regeneración controlada de URLs.
4. **Auditoría de accesos**: quién listó, abrió, descargó y borró cada análisis, con `trace_id`.
5. **Verificación de borrado**: `DELETE /v1/analyses/{id}` debe borrar fila, objeto de video y pose, y confirmarse con un test que inspeccione S3 y Postgres. Hoy la intención existe; se convierte en evidencia.
6. **Sanitización de metadatos** del video (EXIF) y prevención de SSRF en la URL prefirmada.
7. **Cadena de suministro**: `pip-audit`, `govulncheck`, Trivy, SBOM y firma de imágenes en CI.
8. **Roles**: preparar el modelo de `roles` en OIDC que luego usará el portal del entrenador (Plan B, B6), sin activarlo todavía.

**Criterio de salida**: sin secretos en el repositorio ni en logs; cuotas efectivas; borrado verificado en los tres almacenes; vulnerabilidades críticas en CI = 0.

## A4. Observabilidad y operación

- **SLOs/SLIs** (definición, sin cronograma):
  - Disponibilidad de la API ≥ 99,9 %.
  - Tasa de análisis completados ≥ 98 %.
  - p95 de extremo a extremo y p95 por etapa.
  - Presupuesto de error y política de congelación de despliegues.
- **Dashboards nuevos**: duración por etapa, profundidad de cola, longitud de DLQ, tasa de detección de pose, abandonos por `error.code`, distribución de confianza del clasificador, costo por análisis.
- **Trazas enriquecidas**: atributos `analysis.id` (opcionalmente seudonimizado), `stroke_type`, `classifier_id`, `model_version`, `knowledge_base_version`, `metrics.count`.
- **Logs estructurados** con `trace_id`/`span_id` y **redacción de PII**.
- **Alertas**: trabajos sin confirmar, DLQ no vacía, caída de tasa de completados, latencia por etapa por encima del presupuesto.
- **Runbooks** por modo de fallo, con consultas concretas y pasos de recuperación.

**Criterio de salida**: cada error tiene runbook y alerta; cada traza permite reconstruir el análisis completo; el dashboard responde «¿cuántos análisis fallaron ayer y por qué?» sin consultar la BD.

## A5. Cliente web y experiencia de usuario

La web actual (`api/web/static/`) es funcional pero fría: subida con barra de progreso, *polling* y lista plana de hallazgos. Se maximiza así:

1. **Progreso por etapas en tiempo real** vía SSE/WebSocket: `subiendo → pose → clasificando → segmentando → midiendo → aplicando reglas → listo`. Sustituye el `POLL_INTERVAL_MS = 2000`. El worker ya emite un span por etapa; se reutiliza el mismo modelo de eventos.
2. **Visor de informe enriquecido**:
   - Reproductor de vídeo con **superposición del esqueleto** a partir de `poses/<id>.json` (artifact ya generado).
   - Línea de tiempo sincronizada con `start_frame`, `backswing_frame`, `impact_frame`, `end_frame` del contrato.
   - Al hacer clic en un hallazgo, el video salta al fotograma relevante y se resaltan los landmarks implicados.
   - Al hacer clic en un punto fuerte (Plan B), se resalta la cadena cinética que funciona bien.
3. **UI de hallazgos**: filtros por categoría e impacto, evidencia expandible con valor medido y rango objetivo, y distinción visual entre «corregir primero» y «más adelante».
4. **Sección de puntos fuertes** con el mismo nivel visual que los errores (B2).
5. **Diagnóstico y plan** (B3/B4) renderizados como bloque narrativo + plan de entrenamiento con checkboxes.
6. **Evolución**: gráfica de métricas por análisis y de errores resueltos, usando el historial ya disponible en `GET /v1/analyses`.
7. **Guía de grabación previa** con ejemplos de encuadre, luz y ropa; comprobación de calidad en cliente para no gastar infraestructura en videos inválidos.
8. **Estados de error accionables**: cada `error.code` del backend muestra un texto concreto y un botón de reintento o de nueva grabación.
9. **Accesibilidad, i18n ES/EN, responsive y modo oscuro**.
10. **Arquitectura de frontend**: dividir `app.js` en módulos ES (`auth`, `api`, `upload`, `report`, `viewer`, `history`, `drills`) con tests unitarios (Vitest). **No** migrar a un framework completo sin evidencia de que los módulos ya no bastan (KISS/YAGNI).

**Criterio de salida**: el usuario entiende *qué pasó*, *dónde en el video* y *qué hacer* sin leer texto técnico; la lista de hallazgos deja de ser la única salida; sin regresión XSS (seguir usando `textContent`).

## A6. API y contratos HTTP

- Mantener las 10 rutas actuales; añadir solo lo que el Plan B necesita:
  - `GET /v1/analyses/{id}/events` (SSE) o endpoint equivalente para progreso.
  - `POST /v1/analyses/{id}/feedback` para valoración de recomendaciones y drills.
  - `GET /v1/drills` y `GET /v1/drills/{id}` para la biblioteca (B4).
  - `GET /v1/me/progress` para tendencias agregadas.
- **Versionado**: nuevos campos aditivos no cambian `schema_version`; cualquier cambio incompatible obliga a `2.0` y a soporte simultáneo durante la migración.
- **Contratos generados**: validar que el `report` producido por el worker cumple el schema en un test de CI, no solo en tests unitarios.
- **Documentación**: publicar `Docs/CONTRATOS.md` como referencia de API con ejemplos de los nuevos campos.

## A7. Base de datos

- Migraciones versionadas en `api/migrations`, con compuerta en CI: ningún despliegue con migración pendiente.
- Tablas nuevas: `analysis_outbox`, `analysis_events` (si no se usa stream directo), `drills`, `drill_plans`, `drill_plan_items`, `feedback`, `knowledge_reviews`.
- `analyses.report` sigue siendo JSONB; se indexa en lugar de normalizarse, para no romper el contrato.
- Política de retención y borrado en cascada verificable.

## A8. Infraestructura, CI/CD y costos

- **Terraform** para producción: red, bucket S3, Postgres administrado, Redis, secretos, contenedores. El `docker-compose` queda para desarrollo.
- **Orquestación**: contenedores administrados con autoescalado; pool de workers separado; GPU solo cuando se publique `pose_seq` (§5).
- **Despliegues** canary/blue-green con compuertas: contratos válidos, migraciones aplicadas, SLOs sanos; rollback automático.
- **Entornos efímeros por PR** para verificación de contratos y E2E.
- **Pruebas de carga** (`k6`) sobre subida, submit y consulta de reporte.
- **Backup/restore** de Postgres y bucket con RPO/RTO probados en simulacro.
- **Costos**: dashboards por servicio, presupuestos y alertas; revisión periódica del lifecycle de videos.

## A9. Móvil (Android; iOS fuera de alcance)

- Paridad de funcionalidad con la web: visor de esqueleto, puntos fuertes, diagnóstico y plan de drills.
- **Notificaciones push** cuando el análisis termina, sustituyendo el *polling* en segundo plano.
- **Subida offline**: encolar videos cuando no hay red y subirlos al recuperar conexión.
- **Cámara guiada** con superposición para encuadrar como espera la métrica de vista de perfil.
- Tests de `shared` y compilación de APK en CI (ya existen; se amplían a los modelos nuevos del contrato).

**Cierre del Plan A**: plataforma completa, endurecida y lista para recibir los campos del Plan B. El Plan B no empieza hasta que A0–A9 tengan sus criterios de salida cumplidos, según la decisión D4.

---

# PLAN B — CONOCIMIENTO Y EXPERTICIA (se ejecuta completo después del Plan A)

## B0. Fundamento científico y gobernanza del conocimiento

**Objetivo**: convertir la base de conocimiento de «YAML con umbrales provisionales» en «conocimiento experto trazable, revisado y versionado».

1. **Revisión bibliográfica** (ya realizada en este documento, §10.1) y panel de expertos: entrenador titulado, biomecánico y fisioterapeuta deportivo.
2. **Modelo técnico por golpe**: fases, KPIs, rangos normativos por nivel/edad/altura/mano dominante y **grado de evidencia** por regla. Las referencias respaldan direcciones y mecanismos; **no** los números exactos de los umbrales actuales, que siguen siendo provisionales hasta B7.
3. **Esquema de KB v2**, ampliando el actual (`stroke`, `version`, `validated_by`, `rules[]`):

```yaml
stroke: backhand
version: 0.1.0
validated_by: null
evidence_level: provisional        # provisional | literature | expert_validated
rules:
  - id: BH-PREP-001
    title: Preparación sin giro de hombros
    category: fundamental
    impact: 5
    difficulty: 2
    severity: high                  # low | medium | high
    prerequisites: []               # reglas que deben corregirse antes
    caused_by: []                   # reglas que explican esta
    normative_range_by_level:
      beginner: [30, 45]
      intermediate: [45, 65]
      advanced: [65, 90]
    when:
      all:
        - {metric: shoulder_rotation_range, op: lt, value: 45}
    evidence: [R1]                  # IDs de la §10.1
    explanation: ...
    correction: ...
    drills: [BH-UNIT-TURN-1, BH-MEDBALL-1]
    contraindications: []
    example_video: s3://.../examples/BH-PREP-001.mp4
```

4. **Motor de reglas extendido** sin romper el actual: seguir usando el registro de operadores (`lt/lte/gt/gte/eq/between`) y añadir soporte para `normative_range_by_level`, `prerequisites`, `caused_by` y reglas **positivas** (B2). El arranque sigue fallando si una regla cita una métrica no registrada.
5. **Flujo de validación con revisión por pares**: borrador → revisión por el entrenador → ajuste de umbrales con el corpus (B7) → firma en `validated_by` → subida de `version` → changelog. Cada reporte guarda la versión usada (`knowledge_base_version`, ya presente).

**Criterio de salida**: esquema v2 implementado y validado en CI; al menos un golpe con `validated_by != null` y evidencia enlazada; ninguna regla sin `evidence` ni rango normativo.

## B1. Multi-golpe (foco prioritario del Plan B, decisión D5)

El clasificador ya distingue `forehand`, `backhand`, `serve` y `volley` (y `other` = smash), pero `MetricRegistry` solo registra `forehand`, por lo que los demás devuelven `UNSUPPORTED_STROKE`. Se cierra golpe a golpe, con el mismo rigor, usando el corpus de 50–200 vídeos y al entrenador (D12).

### B1.1 Forehand (consolidar y ampliar)

Métricas actuales (7) + nuevas: `preparation_timing`, `racket_lag`, `racket_drop_depth` (depende de B5), `lateral_weight_shift`, `knee_extension_timing`, `head_stability_at_impact`, `contact_height`, `contact_lateral_distance`, `swing_plane`, `balance_at_finish`, `trunk_side_bend`, `off_hand_involvement`, `inter_repetition_consistency` (depende de B5 multi-repetición).

Evidencia de partida: revisión de mecánica y prácticas de aprendizaje del forehand [R1], influencia del apoyo y la postura en la biomecánica de rodilla y riesgo [R7], relación entre fuerza de reacción en suelo y velocidad de bola [R6], efecto de empuñadura y punto de contacto en la producción de fuerza [R17], diferenciasentre jugadores expertos y recreativos en actividad y transmisión de impacto [R18].

### B1.2 Revés (una y dos manos)

`unit_turn_range`, `shoulder_rotation_range`, `knee_angle_min`, `contact_point_ahead`, `off_hand_on_racket` (dos manos), `hip_drive_timing`, `extension_through_shot`, `follow_through_height`, `balance_at_finish`, `kinetic_chain_sequence`.

Reglas iniciales previstas: preparación sin giro, contacto atrasado, revés «de brazo», codo excesivamente flexionado, terminación corta, cadera y hombros en bloque.

### B1.3 Saque

Modelo de 8 fases de Kovacs y Ellenbecker [R2] como esqueleto del modelo técnico: preparación, lanzamiento, carga, «trophy position», impulsión, contacto, pronación/desaceleración y recuperación.

Métricas: `toss_height`, `toss_consistency`, `toss_horizontal_offset`, `trophy_knee_flexion`, `trophy_racket_drop`, `shoulder_external_rotation`, `leg_trunk_arm_sequence`, `contact_height`, `pronation_timing`, `landing_balance`, `foot_fault_risk`.

Evidencia: revisión del movimiento de saque y biomecánica de los tres tipos con implicaciones de lesión [R3], análisis de flujo de energía comparando jugadores lesionados y no lesionados [R4], factores físicos determinantes de la velocidad de saque [R5], rotaciones del tren superior medidas con sensores en jugadores profesionales [R16], marco de evaluación de cadena cinética inferior-superior con IMUs y placas de fuerza [R15].

### B1.4 Volea

`split_step_timing`, `racket_head_above_wrist`, `backswing_length`, `punch_vs_swing`, `contact_point_ahead`, `balance_at_finish`, `volley_readiness`.

**Criterio de salida**: los cuatro golpes con métricas registradas, reglas positivas y negativas, KB creada y un análisis E2E completado sin `UNSUPPORTED_STROKE` para cada uno.

## B2. Detección y comunicación de TODOS los puntos a favor

### B2.1 Principio

Hoy `findings[]` contiene exclusivamente errores. El informe debe incluir **`strengths[]`**, con reglas positivas explícitas. **Un punto fuerte no es la ausencia de un error**: se define con su propio umbral, su evidencia y su explicación de por qué importa.

Reglas de diseño para no inflar los elogios:

1. Solo se reporta si todas sus métricas superan `min_metric_confidence` (mismo filtro que los errores).
2. Solo se reporta si el valor supera claramente el rango normativo del nivel del jugador, no si está «en la media».
3. Máximo **3–5** puntos fuertes por informe, ordenados por impacto y confianza.
4. Se usan para **reencuadrar** el diagnóstico: «Tu giro de hombros es tu mayor activo; el fallo está en el momento del impacto. Apóyate en ese giro…».
5. Se comparan con el historial del jugador («mejoraste 12° en tres análisis») y con rangos por nivel.
6. Nunca se felicita una ejecución que sea simultáneamente lesiva (p. ej. «potentísimo swing» con carga excesiva en codo): las reglas de salud tienen prioridad.
7. Contrato aditivo: `strengths[]` con `{rule_id, rank, category, title, evidence, why_it_matters, how_to_maintain, confidence}`.

### B2.2 Puntos fuertes observables HOY con las 7 métricas actuales

Estos son los que el sistema podría reportar **ya mismo**, con el video que recibe hoy:

| # | Punto a favor | Condición sobre la métrica actual | Por qué importa | Cómo mantenerlo / explotarlo |
| --- | --- | --- | --- | --- |
| S1 | **Excelente preparación con giro de tronco** | `shoulder_rotation_range` ≥ rango alto del nivel | Aporta potencia sin sobrecargar el brazo [R1] | Usar ese giro como base para corregir el punto de contacto |
| S2 | **Torsión eficaz cadera-hombros** | `hip_shoulder_separation_max` alto | Almacena energía elástica en el tronco [R4][R15] | Mantener el hombro girado mientras la cadera inicia |
| S3 | **Brazo extendido en el impacto** | `elbow_angle_at_impact` ≈ 150–170° | Mayor radio de swing y mejor control [R17] | No bloquear el codo; dejar que acompañe el giro |
| S4 | **Apoyo de piernas sólido** | `knee_angle_min` en rango bajo del nivel | Estabilidad e impulso desde el suelo [R6][R7] | Repetir la misma profundidad en cada golpe |
| S5 | **Impacto adelantado** | `contact_point_ahead` alto | Dirección, limpieza y menor esfuerzo del brazo [R17] | Preparar antes para conservar ese punto |
| S6 | **Terminación completa** | `follow_through_height` > umbral sobre el hombro | Profundidad y efecto, freno progresivo del brazo [R1] | Dejar que la raqueta siga el recorrido |
| S7 | **Cadena cinética ordenada** | `kinetic_chain_sequence = 1` | Energía transmitida escalonadamente [R4][R15] | Usarlo como patrón para revés y saque |
| S8 | **Video de alta fiabilidad** | `pose_detection_rate` alto y sin avisos | Las conclusiones son fiables y comparables | Repetir la misma configuración de cámara |

S8 no es un punto fuerte del jugador sino del análisis; se presenta por separado como «confianza del informe», nunca mezclado con S1–S7.

### B2.3 Puntos fuertes que un experto ve y el sistema debe aprender a medir (B1/B5)

- Cabeza estable y mirada fija cerca del impacto (`head_stability_at_impact`).
- Mano no dominante acompañando la preparación (`off_hand_involvement`, ya en B1.1).
- Transferencia de peso y amplitud de apoyo (`lateral_weight_shift`).
- Relajación del brazo antes de la aceleración y aceleración tardía (`racket_lag`).
- «Drop» profundo de la raqueta (`racket_drop_depth`, B5).
- Salida de bola con efecto y trayectoria controlada (B5 con pelota).
- Equilibrio al terminar (`balance_at_finish`).
- Regularidad entre repeticiones (`inter_repetition_consistency`, B5 multi-repetición).
- Lectura anticipada del juego y *split step* (B1.4).
- Empuñadura y punto de contacto coherentes con el efecto buscado [R17][R18].

### B2.4 Puntos fuertes por golpe (adicionales a B1)

| Golpe | Puntos fuertes a detectar |
| --- | --- |
| **Revés** | *Unit turn* completo, dos manos bien coordinadas, empuje de cadera, extensión larga tras el impacto, punto de contacto adelantado |
| **Saque** | Lanzamiento alto y consistente, «trophy position» estable, flexión de rodillas correcta, «racket drop» profundo, secuencia piernas→tronco→brazo, contacto alto, pronación tardía, aterrizaje equilibrado |
| **Volea** | *Split step* anticipado, raqueta por encima de la muñeca, backswing corto, golpe punzante, equilibrio y lectura de la bola |

### B2.5 Implementación

- Reglas positivas en el mismo YAML, con `polarity: strength | error`.
- `RuleEngine.evaluate()` devuelve `Evaluation(fired, strengths, unmeasured_metrics)`; el mismo filtro de confianza para ambos.
- `Prioritizer` genera `strengths[]` con `rank` y los textos `why_it_matters` / `how_to_maintain`.
- Tests: una métrica por debajo del umbral **no** dispara el punto fuerte; una métrica poco fiable **no** dispara ni error ni punto fuerte; el número máximo se respeta.

**Criterio de salida**: informes de los cuatro golpes con 1–5 puntos fuertes verificados por el entrenador, sin elogios sobre mediciones poco fiables y sin contradicción con las reglas de salud.

## B3. Conclusiones de otro nivel: de lista de errores a diagnóstico causal

1. **Grafo de causalidad entre reglas**. Campos `prerequisites` y `caused_by` (B0) permiten detectar la causa raíz en lugar de tres síntomas: *preparación tardía → impacto atrasado → golpeo con brazo*.
2. **Objeto `diagnosis` en el reporte**:

```json
"diagnosis": {
  "summary": "Tu punto fuerte es el giro de hombros. El origen del problema es la preparación tardía, que adelanta el impacto y obliga al brazo a compensar.",
  "root_cause_rule_id": "FH-PREP-001",
  "contributing_factors": ["FH-CONT-001", "FH-ARM-001"],
  "strengths_to_leverage": ["FH-STR-001"],
  "expected_gain": "Corregir la preparación debería mejorar el punto de impacto y reducir la carga en el codo.",
  "confidence": 0.82,
  "limitations": ["La vista no es estrictamente de perfil; el ángulo de codo es orientativo."],
  "unmeasurable": ["velocidad de raqueta", "trayectoria de bola"]
}
```

3. **Severidad compuesta**: `impact × confianza × frecuencia × efecto en el resultado`. La frecuencia llega con la multi-repetición (B5): «aparece en 7 de 10 golpes» pesa más que «aparece en 1».
4. **Pronóstico con incertidumbre**: qué métrica debería moverse al corregir cada causa, sin prometer cifras no validadas.
5. **Honestidad epistémica**: separar **medido**, **inferido** y **no observable con esta grabación**. Si la cámara impide ver la raqueta, el diagnóstico lo dice y no finge un «lag» incorrecto.
6. **Tendencia**: `trend` con evolución por métrica y por regla a lo largo de los análisis del usuario; un error que desaparece se marca como resuelto.
7. **Priorización revisada**: la ordenación actual (categoría → impacto ↓ → dificultad ↑) se mantiene como base, pero pasa a priorizar **causa raíz primero**, luego impacto, luego frecuencia, luego dificultad.

**Criterio de salida**: entrenador y usuario coinciden en que el diagnóstico identifica el problema principal; cada conclusión tiene evidencia trazable; ninguna conclusión afirma haber medido lo que no se midió.

## B4. Recomendaciones y ejercicios de otro nivel

### B4.1 Biblioteca de drills como datos

Sustituir los `drills` actuales (array de cadenas) por entidades con identidad propia, almacenadas en BD (`drills`) y versionadas:

```yaml
id: FH-LAG-2
goal_metrics: [racket_lag, contact_point_ahead]
target_rules: [FH-SYNC-002]
category: tecnico          # tecnico | fisico | tactico | mental
equipment: [raqueta, conos, pelotas]
duration_min: 8
sets_reps: "3 x 10"
difficulty: 3
focus: external            # external | internal | holistic  (ver R11–R14)
coaching_cue: "Siente que la raqueta se queda atrás hasta el último momento."
success_criterion: "Contacto por delante de la cadera en 7/10 golpes"
progressions: [FH-LAG-1, FH-LAG-3]
regressions: [FH-LAG-0]
safety_notes: []
example_video: s3://.../drills/FH-LAG-2.mp4
```

### B4.2 Plan personalizado

- **Plan de 4 semanas** generado desde las 2–3 prioridades del diagnóstico: frecuencia semanal, orden de drills, criterio de avance («cuando `contact_point_ahead` > 0.20 m en 7/10 golpes, pasa al nivel 2»).
- **Adaptación** a nivel, edad, limitaciones físicas, material disponible, mano dominante y carga de entrenamiento.
- **Un foco a la vez**: máximo 1–2 consignas por sesión, evitando las 8 correcciones simultáneas del informe actual.
- **Foco atencional**: priorizar foco externo y consignas de efecto en la bola, respaldado por la revisión sistemática en deportes de raqueta [R11], la discusión sobre foco externo como condición necesaria en atletas [R12] y la evidencia de que el foco óptimo depende del nivel [R14]; cautela con la transferencia literal de laboratorio a entrenamiento real [R13].
- **Componente preventivo** cuando el diagnóstico apunte a sobrecarga de codo/hombro: trabajo de escápula y manguito [R9][R10], con derivación a fisioterapeuta cuando proceda [R19][R20]. Las recomendaciones son de coaching, no diagnóstico médico.
- **Seguimiento**: checkboxes de drills realizados, re-análisis programado y verificación de si la métrica objetivo se movió. El feedback se guarda vía `POST /v1/analyses/{id}/feedback` (A6) y alimenta el bucle de validación (B7).

**Criterio de salida**: cada regla tiene al menos 2 drills con criterio medible y progresión; los planes son distintos para perfiles distintos; el entrenador aprueba la biblioteca antes de publicarla.

## B5. Percepción aumentada y multi-repetición (ola intermedia, decisión D9)

### B5.1 Detección de raqueta y pelota

- **Raqueta**: modelo ONNX de detección/segmentación de la raqueta (cabeza, garganta, puntero) sobre los mismos fotogramas. Respeta las compuertas MLOps existentes y la advertencia de licencia AGPL de la ruta YOLO documentada en `ARQUITECTURA.md`; preferir rutas con licencia permisiva cuando sea posible.
- **Nuevas métricas desbloqueadas**: velocidad de raqueta, `racket_lag`, `racket_drop_depth`, plano del swing, aceleración tardía, distancia raqueta-bola en el impacto.
- **Pelota**: detección y seguimiento para `bounce_relative_timing`, estimación de trayectoria y resultado de la bola (profundidad/dirección aproximadas).
- **Cancha** (opcional, después): líneas para `serve_in_out` y contexto espacial.

### B5.2 Multi-repetición por video

- Sustituir `WristSpeedSegmenter` (un golpe por video) por un segmentador que detecte **3–10 golpes**, con `segmentation_summary` en el reporte.
- Métricas agregadas: media, desviación y rango de cada KPI; `inter_repetition_consistency`.
- Hallazgos con frecuencia: «aparece en 7 de 10 repeticiones», distinguiendo **sistemático** de **ocasional**.
- Evolución del contrato: `segment` pasa a ser un array o se añade `segmentation_summary` aditivo (§6).
- Impacto directo en B3: la severidad compuesta deja de depender de una única muestra.

**Criterio de salida**: un video con varias repeticiones produce un diagnóstico con frecuencias; las métricas de raqueta tienen confianza y su propio nombre en `quality.warnings` cuando no se detecten; el modelo publicado pasa las compuertas de `ARQUITECTURA.md`.

## B6. Portal del entrenador y gobernanza (decisión D11)

- **Roles OIDC** preparados en A3: `coach`, `admin`, `player`. Un entrenador solo ve a sus alumnos; el admin gestiona KB y roles.
- **UI del portal** (integrada en la web, sin framework nuevo):
  - Editor/revisor de YAML con validación en vivo contra el esquema v2 y contra el registro de métricas.
  - Lista de reglas pendientes de validación con su evidencia y rangos.
  - Firma `validated_by` y subida de `version`, con changelog.
  - Adjuntar videos de ejemplo (bueno vs malo) por regla y por punto fuerte.
  - Panel de progreso de alumnos: métricas, errores resueltos, adherencia a drills.
  - Revisión del feedback de los usuarios sobre recomendaciones.
- **Flujo de aprobación**: borrador → revisión por pares → calibración con corpus (B7) → firma → publicación de versión. El reporte siempre guarda la `knowledge_base_version` usada.
- **Trazabilidad**: quién cambió qué umbral, cuándo y con qué evidencia.

**Criterio de salida**: un entrenador puede validar una regla y publicar una nueva versión sin tocar código ni pedir un despliegue; el worker arranca con la KB publicada y el reporte refleja su versión.

## B7. Calibración y validación con 50–200 vídeos (decisión D12)

1. **Corpus**: 50–200 vídeos reales de los cuatro golpes, niveles variados, vista de perfil consistente, con consentimiento y seudonimización.
2. **Extracción**: procesar con el pipeline actual y guardar `poses/<id>.json` (ya se hace) para iterar métricas sin reprocesar vídeo.
3. **Sesiones de calibración**: entrenador revisa vídeo + métricas + regla disparada/no disparada; ajusta operadores y umbrales por nivel; documenta el rationale.
4. **Criterios estadísticos mínimos**: concordancia entrenador–sistema por regla; revisar especialmente las reglas con pocos casos positivos; no publicar umbrales con muestra insuficiente.
5. **Formalización**: `validated_by` relleno, `version` incrementada, `evidence_level: expert_validated`, changelog.
6. **Validación del impacto**: comparar el diagnóstico automático con la evaluación a ciego del entrenador y medir si seguir el plan reduce errores en un re-análisis.

**Criterio de salida**: KB de los cuatro golpes validada por el experto, con umbrales derivados del corpus propio y concordancia documentada; los reportes ya no dicen «umbrales provisionales».

## B8. Contenido editorial y didáctico

- Biblioteca de vídeos anotados, buenos y malos, por regla y por punto fuerte.
- Esqueletos con ángulos superpuestos, generados a partir de `poses/<id>.json`.
- Glosario de términos biomecánicos en lenguaje de jugador.
- Guías «qué es un buen forehand / revés / saque / volea» por nivel.
- Explicaciones del porqué, apoyadas en las referencias de §10.1, sin citar papers dentro del informe al usuario (se resumen en lenguaje llano).

**Cierre del Plan B**: análisis multi-golpe, multi-repetición y consciente del resultado; informe con puntos fuertes, diagnóstico causal y plan de entrenamiento validado por expertos; portal del entrenador operativo.

---

## 5. Clasificador configurable: todas las posibilidades abiertas (decisión D6)

El comportamiento ya se controla desde `worker/src/tennis_worker/config.py` (Pydantic Settings, validación *fail fast*) y se gobierna con un **archivo de configuración por entorno**, sin tocar código. Variables actuales:

| Variable | Valores | Por defecto |
| --- | --- | --- |
| `STROKE_CLASSIFIERS` | lista ordenada: `user`, `pose_seq`, `yolo` | `user` |
| `STROKE_COMBINER` | `first`, `cascade`, `vote`, `verify` | `first` |
| `STROKE_MIN_CONFIDENCE` | (0, 1] | `0.7` |
| `STROKE_VOTE_WEIGHTS` | `pose_seq=2,yolo=1` | peso 1 |
| `POSE_SEQ_MODEL_VERSION` | versión publicada en `models/pose_seq/<versión>/` | vacío = modelo montado |
| `YOLO_MODEL_VERSION` | versión publicada en `models/yolo/<versión>/` | vacío = modelo montado |

### 5.1 Qué hace cada combinador

| Combinador | Comportamiento | Cuándo usarlo |
| --- | --- | --- |
| `first` | Decide el primero que no se abstiene. `user` se abstiene si el usuario no declaró golpe. | Modo asistido estricto |
| `cascade` | Primero que supera el umbral; los siguientes no se ejecutan (ahorra inferencia). | Producción con modelo de confianza alta |
| `vote` | Media ponderada de probabilidades de todos los clasificadores. | Cuando hay dos modelos complementarios y se quiere robustez |
| `verify` | Si el usuario declaró golpe, se usa ese y los modelos solo añaden aviso si discrepan; si no, funciona como `cascade`. | Recomendado cuando haya modelo publicado: respeta la declaración y detecta el resto |

### 5.2 Perfiles de configuración (archivo `deploy/classifier-profiles.yaml`, decidible en caliente por entorno)

```yaml
profiles:
  assisted_user_only:
    STROKE_CLASSIFIERS: user
    STROKE_COMBINER: first
    STROKE_MIN_CONFIDENCE: 0.7

  assisted_with_warning:
    STROKE_CLASSIFIERS: user,pose_seq
    STROKE_COMBINER: verify
    STROKE_MIN_CONFIDENCE: <recommended_min_confidence de meta.json>
    POSE_SEQ_MODEL_VERSION: <versión publicada>

  automatic_pose_seq:
    STROKE_CLASSIFIERS: pose_seq
    STROKE_COMBINER: cascade
    STROKE_MIN_CONFIDENCE: <umbral calibrado que pase las compuertas>

  ensemble_pose_yolo:
    STROKE_CLASSIFIERS: pose_seq,yolo
    STROKE_COMBINER: vote
    STROKE_VOTE_WEIGHTS: pose_seq=2,yolo=1
    STROKE_MIN_CONFIDENCE: <umbral calibrado>

  full_verify:
    STROKE_CLASSIFIERS: user,pose_seq,yolo
    STROKE_COMBINER: verify
    STROKE_VOTE_WEIGHTS: pose_seq=2,yolo=1
```

### 5.3 Compuertas para publicar un modelo (sin cambiar por el hecho de existir)

Según `Docs/pasosSiguientesEnDev.md`, el mejor `pose_seq` del barrido alcanzó macro-F1 0.779, F1 `other` 0.75, `other→serve` 1/15, umbral calibrado 0.9992, cobertura 0.23 y precisión@umbral 0.881; **no pasa** las compuertas (macro-F1 ≥ 0.90, F1 `other` ≥ 0.75 con n=15 ruidoso, `other→serve` ≤ 1, `threshold_reached_target`, precisión@umbral ≥ 0.95 y cobertura ≥ 0.50). YOLO11n (macro-F1 0.639, precisión@umbral 0.741) quedó descartado y además arrastra licencia AGPL-3.0.

**Decisión operativa**: el archivo de configuración permite **elegir el camino en cada entorno** sin recodificar. El perfil por defecto sigue siendo `assisted_user_only`; el cambio a `assisted_with_warning` o `automatic_pose_seq` es una decisión de despliegue documentada, no una modificación de código. Antes de activar cualquier perfil automático se requiere calibración por temperatura y el cumplimiento de las compuertas, o una ADR que acepte explícitamente el riesgo.

**Criterio de salida**: cambiar de perfil = cambiar una variable y reiniciar; el reporte refleja `classification.classifier_id`, `model_version` y `dataset_hash` del camino elegido; los perfiles están cubiertos por tests de arranque.

---

## 6. Evolución del contrato del reporte: las cuatro alternativas (decisión D7)

Se documentan las cuatro opciones planteadas. **Recomendación: alternativa 1**, con la 2 reservada para el futuro si los campos nuevos dejan de ser opcionales.

| # | Alternativa | Qué es | Ventajas | Costes / riesgos |
| --- | --- | --- | --- | --- |
| 1 | **Aditivo 1.1 (recomendada)** | `schema_version` sigue `1.0`; se añaden campos opcionales `strengths`, `diagnosis`, `drill_plan`, `trend`, `segmentation_summary` | Cero ruptura; los clientes actuales ignoran lo desconocido; despliegue independiente de API, worker y móvil | Los clientes antiguos no ven las mejoras hasta actualizarse; requieretests de «ignorar campos desconocidos» |
| 2 | **Ruptura a 2.0** | `schema_version: "2.0"`, `findings` se reorganiza y los campos nuevos pasan a obligatorios | Modelo limpio, sin campos legados | Go, Python y Kotlin deben soportar 1.0 y 2.0 durante la migración; mayor riesgo de despliegue; contradice la política actual de compatibilidad |
| 3 | **Solo interno en el worker** | Campos nuevos solo en el JSON guardado por el worker; la API los oculta | Riesgo mínimo en clientes; útil para experimentación | El usuario no recibe ninguna mejora del Plan B; inconcebible como estado final |
| 4 | **Decisión diferida** | No cambiar el contrato hasta tener datos de la calibración | Evita diseñar con información incompleta | Bloquea A0 y, por dependencia, todo el Plan B; solo aceptable como matiz temporal dentro de la alternativa 1 |

### 6.1 Configuración recomendada (alternativa 1)

```json
"schema_version": "1.0",
"strengths": [
  {
    "rule_id": "FH-STR-001",
    "rank": 1,
    "category": "fundamental",
    "title": "Excelente giro de hombros en la preparación",
    "evidence": {"shoulder_rotation_range": 68.4},
    "why_it_matters": "Aporta potencia sin depender solo del brazo.",
    "how_to_maintain": "Mantén el giro y úsalo para llegar antes al punto de impacto.",
    "confidence": 0.91
  }
],
"diagnosis": {"summary": "...", "root_cause_rule_id": "...", "limitations": []},
"drill_plan": {"weeks": 4, "items": []},
"trend": {"metrics": []},
"segmentation_summary": {"repetitions": 8, "unmeasured": []}
```

### 6.2 Reglas de oro

- Un cambio compatible (campo opcional nuevo) **no** sube `schema_version`.
- Un cambio incompatible sube a `2.0` y obliga a soportar ambas versiones durante la migración.
- `contracts/*.schema.json` es la fuente de verdad; las implementaciones en Go, Python y Kotlin se validan contra los mismos fixtures en CI.
- La alternativa 3 es un **estado temporal de experimentación**, nunca el destino: el Plan B existe para que el usuario vea los resultados.

---

## 7. Orden de ejecución, dependencias y ruta crítica

Decisión D4: **Plan A completo → Plan B completo**. Dentro de cada plan, las olas se ordenan por dependencia, sin fechas ni esfuerzo (D10).

```text
PLAN A
[A0] Contratos y outbox ──► [A1] Fiabilidad ──► [A2] Rendimiento
                                   │                    │
                                   └────────► [A4] Observabilidad
                                                      │
[A3] Seguridad ◄── prepara roles ──────────────────────┤
                                                      ▼
                                          [A5] Web + [A9] Android
                                                      │
                                                      ▼
                                            [A6–A8] API/BD/Infra
        │
        │  (A0 deja el contrato 1.1 listo; A3 deja los roles; A5 deja el visor)
        ▼
PLAN B
[B0] KB v2 + evidencia ──► [B1] Multi-golpe ──► [B7] Calibración 50–200 vídeos
                                   │                       │
                                   ├──────► [B2] Puntos fuertes ◄──────┘
                                   │                       │
                                   ▼                       ▼
                         [B5] Raqueta/pelota + multi-repetición
                                   │
                                   ▼
                    [B3] Diagnóstico causal ◄── [B4] Drills y planes
                                   │
                                   ▼
                 [B6] Portal del entrenador ──► [B8] Contenido editorial
```

**Ruta crítica**: A0 → B0 → B1 → B7 → B2 → B3 → B4. Si A0 se retrasa, todo el Plan B se retrasa.

**Dependencias entre planes**:

| Necesidad del Plan B | La aporta el Plan A |
| --- | --- |
| `strengths`, `diagnosis`, `drill_plan` en el reporte | A0 / §6 |
| Visor con esqueleto y salto al fotograma | A5 (reutiliza `poses/<id>.json`) |
| Multi-repetición y frecuencias | A2 (normalización) + B5 |
| Raqueta/pelota con licencia controlada | A8 (GPU y compuertas MLOps) |
| Roles y aprobación de KB | A3 + A6 |
| Feedback de drills y tendencias | A6 + A7 |
| Calibración con vídeos reales | A2 (ingesta y normalización) |

---

## 8. Métricas de éxito (sin cronograma, decisión D10)

### 8.1 Plataforma

| Métrica | Objetivo |
| --- | --- |
| Disponibilidad de la API | ≥ 99,9 % |
| Tasa de análisis completados | ≥ 98 % |
| p95 de extremo a extremo y por etapa | Definido como SLO en A4 |
| Trabajos huérfanos / duplicados | 0 |
| Cobertura de código | ≥ 80 % (worker y API) |
| Mutantes supervivientes en reglas y priorización | 0 |
| Vulnerabilidades críticas en CI | 0 |
| Costo por análisis | Decreciendo tras A2/A8 |

### 8.2 Conocimiento

| Métrica | Objetivo |
| --- | --- |
| Golpes con KB validada (`validated_by != null`) | 4 |
| Informes con al menos 1 punto fuerte fiable | 100 % de los analizables |
| Puntos fuertes por informe | 1–5, sin elogios sobre métricas poco fiables |
| Reglas con evidencia y rango normativo | 100 % |
| Concordancia entrenador–sistema por regla | Registrada y aceptable para publicación |
| Reducción de errores sistemáticos en re-análisis | Medible y positiva |
| Adherencia a drills y utilidad valorada | Registradas vía feedback |
| Errores por golpe con vídeo de ejemplo | 100 % |

---

## 9. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
| --- | --- | --- |
| Cambio de contrato mal coordinado entre Go, Python y Kotlin | Alto | Aditivo 1.1 + fixtures compartidos + CI de compatibilidad (§6) |
| Umbrales «validados» sin muestra suficiente | Alto | B7 con 50–200 vídeos y concordancia documentada; `evidence_level` explícito |
| Elogios infundados o contradictorios con salud | Medio | Reglas de diseño B2.1; prioridad de reglas de salud |
| Licencia AGPL de la ruta YOLO | Medio | Preferir rutas pose/permisivas; ADR antes de publicar; ya documentado en `ARQUITECTURA.md` |
| Overconfidence del clasificador | Alto | Perfiles de configuración + compuertas §5.3 + calibración por temperatura; modo asistido por defecto |
| Multi-repetición rompa la segmentación actual | Medio | `segmentation_summary` aditivo; conservar `segment` para un solo golpe |
| El portal del entrenador se convierta en un editor de YAML peligroso | Medio | Validación en vivo, revisión por pares y versionado; nunca edición directa sin firma |
| Recomendaciones interpretadas como diagnóstico médico | Alto | Lenguaje de coaching, `contraindications`, derivación a fisioterapeuta y descargo explícito |
| iOS quede como deuda invisible | Bajo | Registrado en §1.2 y comunicado como decisión consciente |

---

## 10. Anexos

### 10.1 Referencias verificadas

Verificadas vía PubMed E-utilities (`esearch`/`esummary`) el 18-11-2026. Se citan con PMID y DOI cuando el registro los contiene. **Respaldan mecanismos y direcciones; no los valores numéricos de los umbrales actuales**, que permanecen provisionales hasta B7.

| ID | Referencia | Uso en el plan |
| --- | --- | --- |
| R1 | Reid M, Elliott B, Crespo M. *Mechanics and learning practices associated with the tennis forehand: a review.* J Sports Sci Med. 2013;12(2):225-231. PMID 24149800; PMC3761830 | B1.1, B2.2, B2.3 |
| R2 | Kovacs M, Ellenbecker T. *An 8-stage model for evaluating the tennis serve: implications for performance enhancement and injury prevention.* Sports Health. 2011;3(6):504-513. PMID 23016050; PMC3445225; doi:10.1177/1941738111414175 | B1.3 (modelo de 8 fases) |
| R3 | Abrams GD, Sheets AL, Andriacchi TP, Safran MR. *Review of tennis serve motion analysis and the biomechanics of three serve types with implications for injury.* Sports Biomech. 2011;10(4):378-390. PMID 22303788; doi:10.1080/14763141.2011.629302 | B1.3 |
| R4 | Martin C, Bideau B, Bideau N, et al. *Energy flow analysis during the tennis serve: comparison between injured and noninjured tennis players.* Am J Sports Med. 2014;42(11):2751-2760. PMID 25167995; doi:10.1177/0363546514547173 | B1.3, B2.2, B3 |
| R5 | Colomar J, Corbi F, Brich Q, Baiget E. *Determinant Physical Factors of Tennis Serve Velocity: A Brief Review.* Int J Sports Physiol Perform. 2022;17(8):1159-1169. PMID 35894981; doi:10.1123/ijspp.2022-0091 | B1.3 |
| R6 | Shimokawa R, Nelson A, Zois J. *Does ground-reaction force influence post-impact ball speed in the tennis forehand groundstroke?* Sports Biomech. 2022;21(7):850-860. PMID 32026748; doi:10.1080/14763141.2019.1705884 | B1.1, B2.2 |
| R7 | Martin C, Sorel A, Touzard P, et al. *Influence of the forehand stance on knee biomechanics: Implications for potential injury risks in tennis players.* J Sports Sci. 2021;39(9):992-1000. PMID 33283656; doi:10.1080/02640414.2020.1853335 | B1.1, B2.2 |
| R8 | Roetert EP, Brody H, Dillman CJ, Groppel JL, Schultheis JM. *The biomechanics of tennis elbow. An integrated approach.* Clin Sports Med. 1995;14(1):47-57. PMID 7712557 | B4.2 (prevención) |
| R9 | Escamilla RF, Andrews JR. *Shoulder muscle recruitment patterns and related biomechanics during upper extremity sports.* Sports Med. 2009;39(7):569-590. PMID 19530752; doi:10.2165/00007256-200939070-00004 | B4.2 |
| R10 | Saini SS, Shah SS, Curtis AS. *Scapular Dyskinesis and the Kinetic Chain: Recognizing Dysfunction and Treating Injury in the Tennis Athlete.* Curr Rev Musculoskelet Med. 2020;13(6):748-756. PMID 32827302; PMC7661674; doi:10.1007/s12178-020-09672-6 | B4.2 |
| R11 | Starzak M, Niźnikowski T, Biegajło M, et al. *Attentional focus strategies in racket sports: A systematic review.* PLoS One. 2024;19(1):e0285239. PMID 38181000; PMC10769023; doi:10.1371/journal.pone.0285239 | B4.2 (foco externo) |
| R12 | Wulf G. *An external focus of attention is a conditio sine qua non for athletes: a response to Carson, Collins, and Toner (2015).* J Sports Sci. 2016;34(13):1293-1295. PMID 26824886; doi:10.1080/02640414.2015.1136746 | B4.2 |
| R13 | Keller M, Schweizer J, Gerber M. *Pay attention! The influence of coach-, content-, and player-related factors on focus of attention statements during tennis training.* Eur J Sport Sci. 2023;23(6):1001-1009. PMID 35343393; doi:10.1080/17461391.2022.2056082 | B4.2 (caution sobre transferencia) |
| R14 | Aiken CA, Becker KA. *Utilising an internal focus of attention during preparation and an external focus during execution may facilitate motor learning.* Eur J Sport Sci. 2023;23(2):259-266. PMID 35164654; doi:10.1080/17461391.2022.2042604 | B4.2 |
| R15 | Kwon JW. *A conceptual field-based framework for lower-upper body kinetic chain assessment in the tennis serve using IMUs and portable force plates.* Front Sports Act Living. 2026;8:1784684. PMID 42022921; PMC13095676; doi:10.3389/fspor.2026.1784684 | B1.3, B2.2 |
| R16 | van Trigt B, Faneker E, Leenen AJR, et al. *Uncovering the hidden mechanics of upper body rotations in tennis serves using wearable sensors on Dutch professional players.* Front Sports Act Living. 2024;6:1463299. PMID 39839546; doi:10.3389/fspor.2024.1463299 | B1.3 |
| R17 | Esser S, Taylor WC, Bertasi RAO, et al. *Effects of Grip Style and Contact Point on Force Production in a Tennis Forehand Groundstroke.* Cureus. 2024;16(11):e74019. PMID 39703250; PMC11658165; doi:10.7759/cureus.74019 | B1.1, B2.2 |
| R18 | Rigozzi CJ, Vio GA, Poronnik P. *Comparison of Grip Strength, Forearm Muscle Activity, and Shock Transmission between the Forehand Stroke Technique of Experienced and Recreational Tennis Players Using a Novel Wearable Device.* Sensors. 2023;23(11):5146. PMID 37299874; doi:10.3390/s23115146 | B1.1, B2.3 |
| R19 | Kim T, Kim YH, Park JM, Choi H. *Comparison of scapular position in elite tennis players with and without shoulder impingement: A case-control study.* J Back Musculoskelet Rehabil. 2023;36(3):685-690. PMID 36617776; doi:10.3233/BMR-220096 | B4.2 (prevención) |
| R20 | Lambrecht Y, Knoche LP, Höller L. *Elite Tennis Players with a Weak Rotator Cuff: The Paradox of Infraspinatus Atrophy - A Clinical Commentary and Practical Approach.* Int J Sports Phys Ther. 2025;20(8):1272-1285. PMID 40756798; doi:10.26603/001c.142211 | B4.2 (prevención y derivación) |

### 10.2 Inventario de archivos que se verán afectados

| Archivo / ruta | Cambio principal |
| --- | --- |
| `contracts/report.schema.json` | Campos opcionales de la alternativa 1 (§6) |
| `contracts/fixtures/` | Fixtures v1.0 y v1.1 para los tres consumidores |
| `worker/src/tennis_worker/config.py` | Perfiles de clasificador (§5), sin cambiar los nombres existentes |
| `worker/src/tennis_worker/pipeline/biomechanics/metrics.py` | Nuevas métricas por golpe (B1) y de raqueta/pelota (B5) |
| `worker/src/tennis_worker/pipeline/biomechanics/registry.py` | Registro de `backhand`, `serve`, `volley` |
| `worker/src/tennis_worker/pipeline/knowledge/engine.py` | `polarity`, `normative_range_by_level`, `prerequisites`, `caused_by` |
| `worker/src/tennis_worker/pipeline/prioritization.py` | Causa raíz + frecuencia + `strengths[]` |
| `worker/src/tennis_worker/pipeline/segmentation.py` | Multi-repetición (B5) |
| `worker/knowledge_base/*.yaml` | KB v2 de los cuatro golpes, con puntos fuertes |
| `api/migrations/` | `analysis_outbox`, `drills`, `drill_plans`, `feedback`, `knowledge_reviews` |
| `api/web/static/app.js` | Modularización, SSE, visor de esqueleto, sección de positivos y plan |
| `mobile/shared/` | Modelos Kotlin del contrato 1.1 y notificaciones |
| `deploy/` | Terraform, perfiles de clasificador, pool GPU |
| `Docs/ARQUITECTURA.md`, `Docs/CONTRATOS.md`, `Docs/BASE_CONOCIMIENTO.md` | Actualización con lo aquí decidido |

### 10.3 Decisiones pendientes que no se han cerrado en este documento

1. **Activación del clasificador automático**: queda gobernada por el archivo de configuración (§5). No se fija una fecha; se decide por compuertas o por ADR que acepte el riesgo.
2. **Ruptura a `schema_version` 2.0**: queda como alternativa 2 documentada (§6), no descartada para el futuro.
3. **iOS**: fuera de alcance por decisión, sin plan de reactivación.
4. **Cronograma y esfuerzo**: excluidos por decisión expresa (D10).
