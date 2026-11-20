## Estado actual (según el repo)

- **Clasificación**: infraestructura lista y configurable (`worker/src/tennis_worker/config.py`: `STROKE_CLASSIFIERS`, `STROKE_COMBINER`, `POSE_SEQ_MODEL_VERSION`, `YOLO_MODEL_VERSION`; `worker/src/tennis_worker/pipeline/classification/*`). Por defecto sigue en modo etapa 1: `STROKE_CLASSIFIERS=user` (`README.md`, `config.py`).
- **ML (pose_seq)**: pipeline DVC completo en `ml/dvc.yaml` (`prepare → extract_poses → features → train_pose_seq → export_pose_seq`) + ruta YOLO (`yolo_dataset → train_yolo`). `ml/params.yaml` ya define 5 clases `[forehand, backhand, serve, volley, other]`, dataset `thetis`, split por jugador y `target_precision: 0.95`.
- **THETIS**: `ml/src/tennis_ml/datasets.py` soporta `dataset.name=thetis` (mapea las 12 acciones a `forehand/backhand/serve/volley/other`, agrupa smash en `other`) y particiona por jugador. Hay poses extraídas en `ml/data/poses/` (indicio de ejecuciones previas), pero no hay un modelo publicado/deployado como artefacto de producción en el flujo descrito.
- **Export/registro/publicación**: `ml/src/tennis_ml/export.py` evalúa en test, calcula `test_macro_f1`/por clase, calibra umbral (`target_precision`), verifica paridad ONNX/PyTorch, guarda `export/pose_seq/{model.onnx,meta.json}`, registra en MLflow (`stroke-pose-seq`) y loguea métricas. `ml/src/tennis_ml/publish.py` sube a `s3://<S3_BUCKET>/models/<clasificador>/<versión>/` y mueve un alias de MLflow (por defecto `production`, no `champion`) al `run` exportado.
- **Carga en worker**: deliberadamente **no** consulta MLflow en runtime; descarga una versión fija desde S3 al arrancar (`worker/src/tennis_worker/infrastructure/model_store.py`, `main.py`: `POSE_SEQ_MODEL_VERSION`/`YOLO_MODEL_VERSION`, caché en `stroke_models_dir`). `Docs/pasosSiguientesEnDev.md` documenta explícitamente que no se debe resolver `models:/…@champion` en el worker por determinismo/reinicio.
- **Base de conocimiento/biomecánica**: solo `worker/knowledge_base/forehand.yaml` (v0.1.0, `validated_by: null`, 7 reglas). El arranque valida que todas las métricas citadas existan en el registro y avisa si la KB no está validada (`worker/src/tennis_worker/main.py`, `pipeline/knowledge/repository.py`).
- **Métricas/registro**: 7 métricas implementadas para forehand en `worker/src/tennis_worker/pipeline/biomechanics/metrics.py`; `registry.py` solo registra `{"forehand": [...]}`. `contracts.StrokeType` permite `forehand/backhand/serve/volley`, pero sin métricas+KB para otros golpes el análisis devolverá `UNSUPPORTED_STROKE`.

## Fase 1 — Publicar el clasificador de golpes (cierre de `STROKE_CLASSIFIERS=user`)

**Objetivo:** modelo `pose_seq` entrenado con THETIS, validado (macro-F1 objetivo en test sobre las 5 clases del producto), publicado y activado en el worker.

1. **Preparar datos y entorno ML**
   - Copiar THETIS RGB a `ml/data/raw/thetis` (estructura que espera `datasets.py`, p.ej. `VIDEO_RGB/<accion>/p<jugador>_*_s<n>.avi`).
   - Verificar `ml/params.yaml`: `dataset.name: thetis`, `classes: [forehand, backhand, serve, volley, other]`, `splits` por jugador, `pose_seq.target_precision`, `version` (vacío = autogenerada).
   - Levantar dependencias/servicios ML (según `README.md`/compose): MLflow + MinIO/bucket accesibles desde `ml` (`MLFLOW_TRACKING_URI`, credenciales S3, `S3_BUCKET`).

2. **Entrenar y exportar**
   - (Primera vez / datos nuevos) versionar raw si aplica: `dvc add ml/data/raw/thetis`.
   - Ejecutar: `dvc repro export_pose_seq` (desde `ml/`, o vía contenedor `--profile ml` como indica README). Esto corre `prepare → extract_poses → features → train_pose_seq → export_pose_seq`.
   - Revisar `ml/metrics/pose_seq.json` y MLflow: `test_macro_f1`, `test_per_class` (F1 por clase), matriz de confusión, `coverage_at_threshold`/`accuracy_at_threshold`, baselines, `test_low_support_classes`. Comprobar el criterio acordado (p.ej. macro-F1 ≥ 0.90 y clases críticas aceptables) antes de promover.
   - Artefactos esperados: `ml/artifacts/pose_seq/`, `ml/export/pose_seq/{model.onnx,meta.json}` (incluye `version`, `classes`, `recommended_min_confidence`, `mlflow_run_id`, `dataset_hash`).

3. **Publicar y trazar en MLflow (ajustado a la arquitectura actual)**
   - Publicar: `python -m tennis_ml publish export/pose_seq` (sube a S3 `models/pose_seq/<version>/` y asigna alias MLflow `production` en `ml/src/tennis_ml/publish.py`).
   - **Discrepancia con el plan propuesto**: el plan pide alias `champion` y carga `models:/<nombre>@champion` en el worker. Hoy eso no encaja: el worker carga versión fija por S3 (`POSE_SEQ_MODEL_VERSION`) y `publish` usa `production`.
     - **Opción recomendada (sin cambiar arquitectura):** mantener alias MLflow para trazabilidad (`production` o `champion`) y desplegar fijando `POSE_SEQ_MODEL_VERSION=<version>`; promoción = cambiar env + reinicio controlado (determinista).
     - **Si se quiere `champion` en MLflow:** extender `publish`/CLI para aceptar alias (p.ej. `--alias champion`) o llamar `publish(..., alias="champion")`; esto solo mejora trazabilidad, no cambia la carga del worker.
     - **Si se quiere resolver alias en runtime:** requeriría cambiar `ModelStore`/arranque para consultar MLflow (o un puntero) y descargar la versión apuntada; implica dependencia de MLflow en arranque y posible no-determinismo entre reinicios (evaluar antes).

4. **Activar en el worker**
   - Configurar: `STROKE_CLASSIFIERS=user,pose_seq` (recomendado: respeta golpe declarado y clasifica si falta) y `STROKE_COMBINER=first`, `POSE_SEQ_MODEL_VERSION=<version>`, bucket/credenciales S3 y `stroke_models_dir` accesibles.
   - Reiniciar worker. Al arrancar descargará `models/pose_seq/<version>/model.onnx+meta.json` (`ModelStore.ensure`) y cargará `PoseSequenceClassifier` (valida `feature_set/seq_len` compatibles).
   - Verificar en un análisis que `report.classification` refleje `classifier_id: pose_seq`, `model_version` y `dataset_hash`, y que golpes no soportados/confianza baja se manejen como espera el combiner/umbral (`stroke_min_confidence`, `recommended_min_confidence` en meta).

**Criterio de cierre Fase 1:** modelo publicado, versión fijada en entorno, worker clasificando automáticamente en el flujo acordado y métricas de evaluación registradas/trazables.

## Fase 2 — Calibrar y validar reglas biomecánicas (forehand)

**Objetivo:** pasar umbrales provisionales a reglas validadas por experto, con evidencia.

1. **Corpus y extracción**
   - Reunir 20–50 videos de forehand (niveles variados, condiciones representativas), preferible vista/perfil consistente con lo asumido por métricas/segmentación.
   - Procesarlos con el worker/pipeline actual para obtener `report.metrics` (7 métricas: `shoulder_rotation_range`, `hip_shoulder_separation_max`, `elbow_angle_at_impact`, `knee_angle_min`, `contact_point_ahead`, `follow_through_height`, `kinetic_chain_sequence`) y revisar `quality`/confianzas.
   - Registrar casos donde la medición no sea fiable (baja detección, oclusiones, ángulo de cámara) para no sesgar umbrales.

2. **Calibración con entrenador**
   - Revisar video + métricas + regla disparada/no disparada (`worker/knowledge_base/forehand.yaml`).
   - Ajustar `when` (operador/valor) y, si aplica, textos de `explanation/correction/drills`; mantener coherencia con unidades y semántica de cada métrica (`metrics.py`).
   - Iterar hasta acuerdo; documentar rationale fuera o en control de cambios del YAML.

3. **Formalizar validación**
   - Editar `worker/knowledge_base/forehand.yaml`: subir `version` (p.ej. `0.2.0` o `1.0.0` según criterio), establecer `validated_by: "<nombre/rol del experto o equipo>"` y fecha/contexto si se desea (el modelo actual solo exige el campo; puede añadirse en comentario/metadatos).
   - Confirmar arranque sin warning "base de conocimiento SIN validar..." y sin errores de métricas desconocidas.

**Criterio de cierre Fase 2:** `forehand.yaml` calibrado sobre corpus real, `validated_by` informado, versión incrementada y comportamiento revisado con el experto.

## Fase 3 — Expandir cobertura a revés y saque

**Objetivo:** métricas, reglas y KB operativas para `backhand` y `serve` (y `volley` si se decide), con el mismo flujo de validación.

1. **Definir métricas por golpe (biomecánica)**
   - Partir de hipótesis apoyadas en literatura (p.ej. brazo no dominante/cadena cinética en revés; carga de piernas, tronco y secuencia en saque) pero tratarlas como provisionales hasta validar.
   - Especificar por métrica: `id`, `label`, `unit`, señal/landmarks usados, ventana temporal (preparación/impacto/follow-through) y criterio de confianza.

2. **Implementar y registrar**
   - Añadir clases de métrica en `worker/src/tennis_worker/pipeline/biomechanics/metrics.py` (o módulos específicos si crece) siguiendo el patrón existente (`Metric.compute(ctx) -> MetricValue`).
   - Registrar en `worker/src/tennis_worker/pipeline/biomechanics/registry.py` (`default_registry`) las listas para `"backhand"` y `"serve"` (y `"volley"` si aplica). Sin registro, `MetricRegistry.compute_all` lanzará `UNSUPPORTED_STROKE`.
   - Añadir tests unitarios en `worker/tests` para cada métrica (valores esperados, confianza, casos borde).

3. **Crear y validar bases de conocimiento**
   - Crear `worker/knowledge_base/backhand.yaml` y `worker/knowledge_base/serve.yaml` con la misma estructura que `forehand.yaml` (`stroke`, `version`, `validated_by: null` inicial, reglas con métricas existentes en el registro).
   - **Importante:** `validate_knowledge` en `main.py` carga todos los YAML y falla al arrancar si una regla cita métricas no registradas para ese golpe; implementar/registrar métricas antes de (o junto con) las reglas.
   - Repetir proceso de Fase 2 por golpe (corpus específico, extracción, sesión con entrenador, ajuste de umbrales, `validated_by` y versión).

4. **Verificar pipeline extremo a extremo por golpe**
   - Clasificación: `ml/params.yaml` y THETIS ya contemplan `backhand/serve/volley/other`; confirmar rendimiento por clase en `metrics/pose_seq.json` y cobertura del combiner.
   - Segmentación: `WristSpeedSegmenter` es genérico (pico de muñeca dominante, asume un golpe/video) — validar que funcione razonablemente para revés/saque o adaptar si es necesario.
   - Probar con videos reales por golpe y revisar `report.stroke_type`, métricas, reglas disparadas y advertencias de calidad.

**Criterio de cierre Fase 3:** `backhand` y `serve` con métricas registradas, KB creadas y calibradas/validadas, y análisis completado sin `UNSUPPORTED_STROKE` para esos golpes.

## Hoja de ruta consolidada (ejecutable)

| Fase | Entregable verificable | Evidencia en repo/runtime |
|---|---|---|
| 1. Modelo publicado | THETIS entrenado, métricas aceptables, artefacto exportado/publicado, worker en modo automático | `ml/metrics/pose_seq.json` + MLflow (`stroke-pose-seq`), `models/pose_seq/<version>/` en S3, `POSE_SEQ_MODEL_VERSION` + `STROKE_CLASSIFIERS=user,pose_seq`, `report.classification` correcto |
| 2. Forehand calibrado | Umbrales ajustados con experto y KB validada | `worker/knowledge_base/forehand.yaml` con `version` incrementada y `validated_by` != null, sin warning de arranque |
| 3. Multi-golpe | Revés y saque soportados end-to-end | Métricas en `biomechanics/metrics.py` + registro en `registry.py`, `backhand.yaml`/`serve.yaml` validados, análisis exitoso por golpe |

**Riesgos/notas a tener en cuenta:** alineación alias (`production`/`champion`) vs carga por versión fija; calidad y representatividad del dataset (incluido gap THETIS→videos de usuarios); supuestos de `WristSpeedSegmenter` y de cámara/vista; `knee_angle_min` usa fase de preparación (coherente con regla actual) — validar para saque/revés; mantener cobertura de tests (CI espera worker con cobertura alta) al añadir métricas/golpes.
