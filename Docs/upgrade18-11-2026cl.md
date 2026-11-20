# Plan maestro: Plataforma (Punto 1) + Conocimiento experto (Punto 2)

## Contexto
La app analiza videos de golpes de tenis. Hoy:
- **Plataforma**: API Go (Gin, ports/adapters) → presigned PUT a MinIO → Redis Streams → worker Python (MediaPipe Pose → segmentación → métricas → reglas YAML → priorización) → reporte JSONB en Postgres → polling desde web (`api/web/static`) y Android (KMP). Auth Keycloak (PKCE), OTel + Grafana, CI con 4 jobs. Buena base, pero con brechas de producción.
- **Conocimiento**: solo **derecha** tiene métricas (7, en `worker/src/tennis_worker/pipeline/biomechanics/metrics.py`) y 8 reglas de error en `worker/knowledge_base/forehand.yaml` (v0.1.0, umbrales "PROVISIONALES", `validated_by: null`). No hay fortalezas, puntajes, fases detalladas, progreso ni visualización. El texto es estático y genérico.

Objetivo: (1) llevar la plataforma a nivel producción y maximizar la experiencia; (2) convertir el análisis en un **diagnóstico de nivel entrenador**: fases, fortalezas, errores con causa raíz, prescripción de ejercicios progresiva y seguimiento.

---

# PUNTO 1 — PLATAFORMA

### 1.A Higiene inmediata (semana 1)
1. Agregar `.env.bak-*` a `.gitignore` (hoy `.env.bak-20261119-111643` no está ignorado) y commitear en bloques lógicos el trabajo pendiente (preferences, ml diagnostics, cambios en mobile).
2. **Corregir la trampa de "Detectar automáticamente"**: ocultar la opción en web (`app.js`) y en Android (`MainActivity.kt`) mientras `STROKE_CLASSIFIERS=user`. Exponer las capacidades con `GET /v1/capabilities` (golpes soportados, autodetección on/off) y que ambos clientes construyan la UI desde ahí.
3. Alinear el alias de MLflow (`champion` en el plan vs `production` en `publish.py`).

### 1.B Seguridad y robustez (semanas 1–3)
- Middleware en `api/internal/httpapi/router.go`:
  - headers CSP estricta, HSTS, nosniff, X-Frame-Options y Referrer-Policy;
  - CORS explícito;
  - rate limit por usuario y por IP con Redis (token bucket), más estricto en `POST /analyses`;
  - cuota diaria de análisis por usuario.
- Limitar el tamaño en el origen: reemplazar el presigned PUT por un **presigned POST con `content-length-range` y content-type** (`api/internal/storage/s3.go`).
- Tokens web: mover a memoria con refresh silencioso, o a un BFF con cookie httpOnly. Hoy viven en localStorage (`auth.js`).
- MinIO: policy mínima por bucket/prefijo (hoy es `readwrite` global), fijar versiones de imágenes y no exponer los puertos 9000/9001/8180 en 0.0.0.0.
- Keycloak para producción: `start` en lugar de dev, sin password-grant fuera de CI, registro con verificación de email.
- **Job de limpieza**:
  - filas `awaiting_upload` con más de 24 h;
  - regla de lifecycle de S3 para videos huérfanos;
  - retención de videos configurable.
- **GDPR**: `DELETE /v1/me` (borra filas y objetos) y `GET /v1/me/export`.
- Contrato worker↔DB: que el worker escriba resultados solo a través de un endpoint interno o de una tabla `analysis_results` propia, para desacoplarse de las migraciones de la API.

### 1.C Experiencia de usuario (semanas 2–6)
- **Tiempo real**: SSE `GET /v1/analyses/:id/events`, con el worker publicando el progreso por etapa en Redis pub/sub. En Android, push con FCM al completar.
- **Reporte visual**:
  - reproductor con overlay del esqueleto (el worker ya guarda `poses/{id}.json`);
  - marcadores de fases en la línea de tiempo;
  - capturas de frames clave (preparación, impacto, terminación);
  - cámara lenta.
- Mostrar lo que hoy se oculta: `segment`, `evidence`, `classification` y la versión de la KB.
- **Dashboard de progreso**: evolución de métricas y puntajes por golpe a lo largo del tiempo; comparación entre dos análisis lado a lado.
- **Guía de grabación interactiva**: chequeo previo en cliente de fps, orientación y encuadre, y validación rápida de la pose en los primeros frames antes de subir.
- **Recorte en cliente**: subir solo la ventana de ±2 s alrededor del golpe, lo que reduce costo y tiempo.
- i18n (es/en), accesibilidad y PWA instalable.
- **Roles**: `coach` puede ver los análisis de sus alumnos (invitación), comentar sobre el reporte y anotar sobre el frame. Esto es la base del Punto 2 (experto en el loop).

### 1.D Infra, CI/CD y operación (semanas 3–8)
- Dockerfiles multi-stage para api y worker, build y push a un registry en CI.
- Escaneo de seguridad: govulncheck, pip-audit, Trivy, gitleaks.
- Entornos staging y producción: compose o k8s/Fly/Render; Postgres administrado y R2 configurado.
- Migraciones en un paso separado, no al arrancar la API, para soportar múltiples réplicas.
- Worker con GPU opcional y autoescalado por longitud de cola (la métrica ya existe en `queue/metrics.go`).
- **SLOs**: p95 del tiempo de análisis y tasa de `failed`. Alertas extra para error rate de la API y latencia del worker.
- Backups de Postgres con prueba de restore.
- Pruebas e2e en CI usando `scripts/e2e.py` contra el compose, más tests de carga con k6.
- Mobile: correr Android contra el backend (Paso 5 pendiente), agregar Crashlytics y evaluar iOS (los targets KMP ya existen).

### 1.E Modelo de clasificación (en paralelo)
- Calibración por temperatura de `pose_seq` (siguiente paso documentado).
- Ampliar el dataset con clips propios etiquetados desde la app: feedback del usuario sobre "¿era este golpe?".
- Publicar solo si pasa los gates de `pasosSiguientesEnDev.md`.
- Mantener el modo `verify` como default.

---

# PUNTO 2 — CONOCIMIENTO EXPERTO Y CALIDAD DEL ANÁLISIS

### 2.A Nuevo modelo de reporte (contrato v2.0)
Ampliar `contracts/report.schema.json` (versión mayor, con convivencia v1/v2 en los clientes):
```
phases[]      {id: ready|split_step|unit_turn|backswing|forward_swing|contact|follow_through|recovery,
               start_ms, end_ms, score 0-100, observations[]}
scores        {overall, technique, timing, balance, power_chain}   // 0-100 + level band
strengths[]   {strength_id, phase, title, why_it_matters, evidence{metric:value}, confidence}
findings[]    (+ phase, severity, root_cause_of[], symptoms[], confidence)
prescription  {focus_of_the_week, drills[]{drill_id, sets, reps, progression_step}, next_review}
key_frames[]  {phase, frame, annotations[]}
comparison?   {previous_analysis_id, deltas[]}
level         beginner|intermediate|advanced (declarado o inferido)
```
En la KB se agregan `category: fortaleza` y las categorías de error `tecnica | sincronizacion | equilibrio | tactica_postura`.

### 2.B Métricas biomecánicas nuevas (worker `biomechanics/`)
Por fase, con la derecha primero y luego el resto de golpes:

- **Preparación**
  - split-step: salto vertical de cadera/tobillos y su timing respecto al golpe;
  - unit turn: rotación de hombros **y** de caderas;
  - posición del brazo no dominante (paralelo a la red);
  - altura de la raqueta/muñeca en el backswing (bucle).
- **Base**
  - tipo de stance (abierto/semiabierto/cerrado) por el vector entre pies frente a la dirección de golpe;
  - ancho de base relativo a la altura;
  - flexión de rodillas.
- **Cadena cinética**
  - secuencia de picos de velocidad angular cadera→tronco→hombro→codo→muñeca, con **desfases en ms** (no solo un 1/0);
  - "lag" de la muñeca.
- **Contacto**
  - altura del punto de impacto (cadera–hombro ideal);
  - distancia lateral al cuerpo;
  - punto delante del pie de apoyo;
  - estabilidad de la cabeza (desplazamiento ±100 ms);
  - codo (extendido o doblado según el estilo).
- **Equilibrio y peso**
  - transferencia del centro de masa atrás→adelante;
  - inclinación del tronco;
  - verticalidad al terminar.
- **Terminación y recuperación**
  - envolvimiento (muñeca sobre el hombro contrario);
  - tiempo hasta volver a la posición neutral.
- **Velocidad**
  - velocidad pico de la muñeca normalizada por altura (proxy de potencia);
  - suavidad (jerk).
- **Futuro**: detección de raqueta y pelota (YOLO fine-tune) para el plano de swing y la altura real de la pelota.

Cada métrica sigue el patrón de `registry.py`: id, unidad, landmarks y confianza por visibilidad.

### 2.C Catálogo de FORTALEZAS (lo positivo observable hoy)
Reglas `category: fortaleza` en la KB, evaluadas con el mismo `knowledge/engine.py`. Siempre se reportan al menos 2–3 cuando existan; reemplazan el texto fijo "¡Buen golpe!".

Detectables **ya** con las 7 métricas actuales de la derecha:

| ID | Fortaleza | Condición (umbral a calibrar) |
|---|---|---|
| FH-STR-001 | Buena rotación de hombros en la preparación | `shoulder_rotation_range ≥ 70°` |
| FH-STR-002 | Separación cadera-hombro que genera potencia elástica | `hip_shoulder_separation_max ≥ 25°` |
| FH-STR-003 | Cadena cinética bien secuenciada (cadera→hombro→muñeca) | `kinetic_chain_sequence == 1` |
| FH-STR-004 | Punto de contacto adelantado | `contact_point_ahead ≥ 0.2 m` |
| FH-STR-005 | Terminación completa y alta | `follow_through_height ≥` altura del hombro |
| FH-STR-006 | Flexión de piernas activa (carga) | `knee_angle_min` entre 110° y 145° |
| FH-STR-007 | Brazo de golpeo en rango eficiente al impacto | `elbow_angle_at_impact` entre 100° y 170° |

Fortalezas que se agregan con las métricas nuevas de 2.B:
- split-step a tiempo;
- unit turn temprano;
- uso del brazo no dominante;
- base estable y ancha;
- cabeza quieta en el impacto;
- transferencia de peso hacia delante;
- buen equilibrio al terminar;
- recuperación rápida;
- swing fluido (jerk bajo);
- alta velocidad de muñeca relativa;
- contacto en la zona de altura ideal;
- stance adecuado al tipo de golpe.

Cada fortaleza explica **por qué importa** (por ejemplo: "esto te da potencia sin forzar el brazo y protege tu codo") y cómo **apalancarla**.

### 2.D Diagnóstico de errores de nivel experto
- **Árbol causa-raíz**: reglas con `root_cause_of` (por ejemplo, poca rotación → contacto tardío → terminación corta). Se muestra 1 causa raíz y sus síntomas, no 3 errores sueltos.
- **Severidad graduada** (leve/moderado/importante) según la distancia al umbral, en lugar de binaria.
- **Umbrales por nivel y por estilo** (por ejemplo, codo recto vs doblado, semi-western vs eastern). Incluir edad y lesiones declaradas en las preferencias para filtrar consejos de riesgo (codo/hombro).
- **Puntaje por fase y global**: media ponderada de las métricas normalizadas contra rangos de referencia por nivel.
- **Comparación** contra perfiles de referencia por nivel (bandas de percentil construidas desde el corpus validado) y contra el análisis anterior del mismo usuario.

### 2.E Prescripción de ejercicios (catálogo de drills)
- Nuevo `worker/knowledge_base/drills.yaml`, versionado. Cada drill tiene:
  - id, objetivo (métrica/fase), nivel, material, lugar (cancha/casa/gimnasio), duración, series/reps;
  - criterios de éxito, **progresión** (paso 1→3), errores comunes y video demostrativo.
- Tipos de drill:
  - técnica con sombra (shadow swing);
  - drills con canasto/feed;
  - **físico específico**: movilidad torácica, fuerza de core rotacional con banda/balón medicinal, pliometría para el split-step, equilibrio unipodal;
  - **dinámicas de juego** (por ejemplo, cruzado con objetivo, "solo derechas adelantadas");
  - **cues mentales** (frases clave).
- Las reglas referencian `drill_id` en lugar de texto libre.
- El prioritizador genera un **plan semanal**:
  - 1 foco principal (la causa raíz de mayor impacto y menor dificultad);
  - 2–3 drills progresivos;
  - 1 ejercicio para consolidar la fortaleza principal;
  - fecha sugerida de re-análisis.

### 2.F Narrativa personalizada (opcional, fundamentada)
- Capa LLM (Claude) que **solo redacta** a partir del JSON estructurado: fortalezas, causa raíz, plan. Tono según el nivel y sin inventar métricas.
- La KB sigue siendo la fuente de verdad.
- Se guarda el texto generado junto con la versión del prompt. Hay fallback al texto estático.

### 2.G Proceso con expertos (el valor diferencial)
1. **Corpus de referencia**: 50–100 videos por golpe, de varios niveles, etiquetados por entrenadores con fases, errores, fortalezas y nivel. Se usa una herramienta interna de anotación (rol `coach` de 1.C).
2. **Panel multidisciplinario**: entrenador certificado (ITF/PTR/RPT), biomecánico y preparador físico. Firman cada regla (`validated_by`, `evidence_refs` con bibliografía: Elliott, Knudson, Kovacs, ITF Coaching).
3. **Calibración**: ajustar umbrales comparando la salida del motor con las etiquetas de los expertos. Métrica de acuerdo: precisión y recall por regla, objetivo ≥ 0.8. Script en `ml/` o `worker/` que corre en CI cuando cambia la KB.
4. **Ciclo continuo**:
   - feedback "¿te sirvió esta recomendación?";
   - correcciones de entrenadores sobre reportes reales;
   - revisión mensual de la KB con versionado semántico.
5. **Expansión de golpes**: revés a 1 y 2 manos, saque (trofeo, pronación, salto, lanzamiento), volea (split-step, impacto adelante y sin swing) y luego smash. Cada golpe tiene su YAML de reglas, fortalezas y drills.

### 2.H Frontend del análisis
El orden de lectura del reporte es:
1. resumen (puntaje global y nivel);
2. **lo que haces bien** (fortalezas primero);
3. foco principal;
4. línea de tiempo por fases con puntaje;
5. errores con evidencia visual sobre el frame;
6. plan de ejercicios con videos;
7. progreso respecto al análisis anterior.

---

## Roadmap sugerido
| Etapa | Plataforma | Conocimiento |
|---|---|---|
| 0–2 sem | 1.A, rate limit/headers, upload limitado | Contrato v2, fortalezas con las 7 métricas actuales (2.C), mostrar evidencia y segmento |
| 2–6 sem | SSE/push, overlay y frames clave, limpieza/GDPR | Métricas nuevas de la derecha (2.B), fases con puntaje, drills.yaml + plan semanal |
| 6–10 sem | CI/CD con imágenes, staging, rol coach | Corpus + calibración con expertos, árbol causa-raíz, umbrales por nivel |
| 10+ sem | Escalado del worker, iOS, dashboard de progreso | Revés/saque/volea, narrativa LLM, raqueta/pelota |

## Archivos clave
- Contratos: `contracts/report.schema.json`, `Docs/CONTRATOS.md`
- Worker:
  - `pipeline/orchestrator.py`, `segmentation.py`, `biomechanics/{metrics,registry}.py`
  - `knowledge/{engine,models}.py`, `prioritization.py`
  - `knowledge_base/forehand.yaml`, nuevo `knowledge_base/drills.yaml`
- API: `httpapi/router.go` (middlewares, SSE, capabilities), `storage/s3.go` (presigned POST), nuevas migraciones (retención, roles, feedback)
- Clientes: `api/web/static/app.js`, `mobile/shared/.../model/Models.kt`, `androidApp/.../MainActivity.kt`

## Verificación
- **Unit tests**: worker (cobertura ≥ 80%), una métrica nueva por test con secuencias de pose sintéticas, reglas de fortaleza y prescripción. Go: middlewares (rate limit, headers) y presigned POST.
- **Contrato**: fixtures v2 validados desde Go, Python y Kotlin (el patrón actual de contract tests).
- **e2e**: `scripts/e2e.py` sube un video de derecha y verifica `strengths.length ≥ 1`, `phases` y `prescription`.
- **Calibración**: el script de acuerdo con expertos corre en CI cuando cambia `knowledge_base/` y falla si baja de 0.8.
- **Manual**: levantar `deploy/docker-compose.yml` y revisar el reporte en la web y en Android.
