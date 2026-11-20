# Pasos siguientes en dev

Con las fases 0–4 construidas, lo que queda no es añadir arquitectura, sino **cerrar los frentes que separan el repositorio de un sistema validado y operativo**. El orden importa: cada paso desbloquea el siguiente.

**Estado a 2026-11-16**

| Paso | Estado |
|---|---|
| 0. Sanear entorno | ✅ Cerrado |
| 1. Primer commit + CI verde | ✅ Cerrado |
| 2. THETIS + `dvc repro` (dataset + entrenamiento) | ✅ Cerrado: `dvc.lock` generado, `dvc push` (1,980 ficheros), `stroke-pose-seq` v2 en MLflow |
| 3. Evaluar y decidir publicación | 🔴 No publicar: mejor pose_seq macro-F1 0.779, F1 `other` 0.75; YOLO 0.639 (§3.6); ninguno pasa las compuertas |
| 4. Publicar y activar la clasificación automática | ⏳ |
| 5. Android en emulador/dispositivo | ⏳ |
| 6. iOS | ⏸️ Fuera de alcance en dev/staging |

---

### 🧹 Paso 0: Sanear el entorno de desarrollo — ✅ cerrado

- ✅ **Visual C++ Redistributable:** actualizado de 14.36 a **14.44.35211** (`msvcp140.dll` y `vcruntime140.dll` en 14.44). `torch` y `onnxruntime` ya importan en local sin error de DLL.
- ✅ **PATH con comillas sueltas:** corregido en el PATH de usuario el 2026-11-16. `gradlew test` en `mobile/` pasa sin parches (las terminales abiertas antes del cambio conservan el PATH viejo).

**Criterio de salida:** cumplido. `pytest` (en el contenedor de ML) y `gradlew test` corren sin parches manuales en el comando.

---

### 🔀 Paso 1: Primer commit y CI en verde — ✅ cerrado

- Commit `2f67d7a` en `origin/main`. Incluye `.gitattributes` y los contratos JSON (`contracts/*.schema.json` + fixtures). No incluye datos de THETIS, `mlruns/`, `mlartifacts/`, `.env` ni `local.properties`. Los únicos `.onnx` versionados son dos fixtures de test de menos de 400 bytes.
- Primera ejecución del CI: **api, worker, ml y mobile en verde** (runners Ubuntu).
- **iOS no tiene job**, por decisión: en dev y staging solo Android. Los runners macOS cuestan unas 10 veces más por minuto y la app iOS no existe todavía (ver Paso 6).

**Criterio de salida:** cumplido.

---

### 🎾 Paso 2: THETIS + `dvc repro` dentro de Docker (fusiona los antiguos "exportar poses" y "entrenar")

`dvc repro export_pose_seq` no exporta solo secuencias de pose: ejecuta la cadena completa **`prepare → extract_poses → features → train_pose_seq → export_pose_seq`**. Es decir, también **entrena, evalúa en test, exporta a ONNX y registra el modelo en MLflow**. Por eso los antiguos Pasos 2 y 3 se ejecutan en una sola operación, y el Paso 3 queda como evaluación y decisión.

#### 2.1. Dataset — ✅ colocado y verificado

`ml/data/raw/thetis/VIDEO_RGB/<acción>/p<N>_<acción>_s<n>.avi` (3.7 GB):
- **1,980 clips**: 12 subdirectorios × 165 `.avi`.
- **55 jugadores**: cada uno con las 12 acciones × 3 repeticiones = 36 clips.

El loader (`ml/src/tennis_ml/datasets.py`) recorre las subcarpetas buscando los nombres de las acciones, así que esta estructura funciona tal cual.

#### 2.2. Lo que hace `prepare` — ✅ revisado

**Mapeo 12 → 5 clases.** Lo aplica `prepare` (`THETIS_CLASSES` en `datasets.py`), y `params.yaml → classes` fija el orden:

| Clase del producto | Acciones THETIS | Clips |
|---|---|---|
| `forehand` | forehand_flat, forehand_openstands, forehand_slice | 495 |
| `backhand` | backhand, backhand2hands, backhand_slice | 495 |
| `serve` | flat_service, kick_service, slice_service | 495 |
| `volley` | forehand_volley, backhand_volley | 330 |
| `other` | smash (no soportado → `UNSUPPORTED_STROKE` en el worker) | 165 |

**Partición por jugador, no por clip.** Así el mismo jugador nunca aparece en train y test, que es metodológicamente mejor que repartir por clip y parecido al LOSO. Con `seed: 42` y 70/20/10 el reparto es determinista. Lo calculé ejecutando `prepare` sobre el dataset real:

| Split | Jugadores | Clips | forehand | backhand | serve | volley | other |
|---|---|---|---|---|---|---|---|
| train | 39 | 1,404 | 351 | 351 | 351 | 234 | 117 |
| val | 11 | 396 | 99 | 99 | 99 | 66 | 33 |
| test | **5** | **180** | 45 | 45 | 45 | 30 | **15** |

Como cada jugador ejecuta todas las acciones el mismo número de veces, **ningún split se queda sin una clase**, y la proporción de clases es la misma en los tres. El test tiene **180 clips, no los 198** de la partición por clip de la literatura. Por eso las cifras no son directamente comparables con esos papers.

#### 2.3. Lanzar el pipeline dentro de Docker

`.dvc/config` apunta a `http://minio:9000` y el entrenamiento registra en `http://mlflow:5000`. Ambos solo se resuelven dentro de la red del compose. La imagen de ML ya trae `/models/pose_landmarker_full.task` y monta el repo en `/workspace`, con directorio de trabajo `/workspace/ml`.

```bash
# desde la raíz del repo
docker compose -f deploy/docker-compose.yml up -d minio mlflow
docker compose -f deploy/docker-compose.yml --profile ml run --rm ml dvc repro export_pose_seq
docker compose -f deploy/docker-compose.yml --profile ml run --rm ml dvc push
```

- `dvc repro` **no sube nada** al remoto: el `dvc push` es un paso explícito.
- Recuerda publicar solo los puertos necesarios: otros stacks del host ocupan 6379, 5433 y 8000–8007.
- `extract_poses` sobre 1,980 clips en CPU es la etapa lenta. Las etapas `yolo_*` no forman parte de esta ruta.

#### 2.4. Verificar la cadena

- `dvc.lock` registra el hash de `data/raw/thetis` y de cada salida.
- `dvc push` completa y los objetos aparecen en el bucket `tennis-ml/dvc` de MinIO.
- MLflow tiene un run en el experimento de `train_pose_seq` y una nueva versión del modelo registrado **`stroke-pose-seq`**.
- `ml/metrics/pose_seq.json` existe y `ml/export/pose_seq/` contiene el ONNX y su metadata. La etapa ya comprueba la paridad PyTorch↔ONNX con el clasificador del worker; si divergen, falla.

**Criterio de salida:** `dvc repro export_pose_seq` completa sin errores, `dvc push` sube los artefactos, `dvc.lock` refleja el dataset real y `stroke-pose-seq` tiene una versión nueva en MLflow.

---

### 🧠 Paso 3: Evaluar el modelo y decidir si publicarlo (criterio reformulado)

La tarea es **macro-F1 sobre 5 clases con split por jugador (5 jugadores, 180 clips de test)**. El objetivo ≥ 0.90 sigue siendo razonable, pero hay que interpretarlo con contexto.

#### 3.1. Métricas que emite el pipeline

`export_pose_seq` escribe todo en `metrics/pose_seq.json` y en el run de MLflow, con nombres planos para comparar runs:

| Métrica | Clave en el JSON | En MLflow |
|---|---|---|
| Macro-F1 y accuracy en test | `test_macro_f1`, `test_accuracy` | igual |
| **F1 por clase y soporte** | `test_per_class.<clase>.{f1,support}` | `test_f1_<clase>` |
| Clases con menos de 20 clips en test (F1 poco fiable) | `test_low_support_classes` | — |
| Matriz de confusión | `confusion_matrix` | en el JSON del run |
| Umbral calibrado en val, cobertura y accuracy con umbral | `threshold`, `coverage_at_threshold`, `accuracy_at_threshold` | igual |
| **Tamaños de split** (jugadores, clips, clases) | `splits.<split>.{players,clips,classes}` | `<split>_players`, `<split>_clips` |
| **Baselines** (entrenados en train, medidos en test) | `baselines.majority`, `baselines.logreg` | `baseline_<nombre>_{macro_f1,accuracy}` |

El código está en `ml/src/tennis_ml/evaluation.py`, y `export_pose_seq` ahora depende también de `data/labels.csv` y de `dataset.seed`.
- `majority` predice siempre la clase más frecuente en train. Con THETIS da un macro-F1 trivial ≈ 0.11.
- `logreg` es una regresión logística sobre la media y la desviación temporal de cada característica del clip. Es un baseline con sentido, porque usa la misma pose pero **sin dinámica temporal**. Si el GRU no lo supera con claridad, la parte secuencial no aporta.

Con THETIS, `test_low_support_classes` incluirá `other` (15 clips): es esperado y hay que mencionarlo al reportar.

#### 3.2. Interpretar el resultado con honestidad

- **No es comparable con la literatura.** Los benchmarks citados (macro-F1 0.913 en diagnóstico de errores, 94.8% en fases, 84.10% accuracy con Inception+LSTM en LOSO) usan otras tareas, número de clases o particiones. Un 0.90 sobre 5 clases agrupadas es una tarea más fácil que 12 clases, y no se debe presentar como equivalente.
- **La clase `other` es la minoritaria** (165 clips en total, **15 en test**). Con 15 clips, un solo error mueve su F1 varias centésimas, así que su F1 es ruidoso y hay que decirlo al reportarlo. Si el macro-F1 es 0.90 pero el F1 de `other` es menor que 0.75, el agregado engaña. En ese caso: class weights o aumentación específica, o documentar la limitación. Para el producto importa sobre todo que el smash no se clasifique como `serve`, porque se analizaría con métricas que no le corresponden. Por eso conviene revisar esa celda de la matriz de confusión.
- **Monitoriza el macro-F1, no solo la accuracy.** Con este desbalanceo leve, las dos pueden separarse.

#### 3.3. Si no llega a 0.90

1. Prioriza la **aumentación temporal** y la **normalización de esqueleto** antes que añadir capas. La variabilidad entre los 55 sujetos es el principal factor de error.
2. Prueba `STROKE_COMBINER=vote` o `verify` con el clasificador YOLO, que se configura sin cambiar código. Requiere construir la imagen con `ML_WITH_YOLO=true` y, en la práctica, una GPU para `train_yolo`.
3. Documenta la brecha y la estrategia antes de publicar.

**Criterio de salida:** el run de test reporta macro-F1, accuracy, F1 por clase, matriz de confusión, tamaños de split y baseline. **Publicar si** macro-F1 ≥ 0.90, el F1 de `other` es aceptable (≥ 0.75, reportado como ruidoso por n=15) y el modelo supera claramente el baseline. Si no, documentar la brecha y la decisión.

#### 3.4. Compuerta de seguridad añadida (2026-11-17)

El run actual tiene un problema que el macro-F1 no mostraba: `calibrate_threshold` no encontró ningún
umbral con precisión 0.95 en validación y **cayó al fallback 0.99** (`export.py`). En test, con ese
umbral, `coverage_at_threshold=0.70` pero `accuracy_at_threshold=0.8492`: el worker aceptaría
aproximadamente 1 de cada 6 predicciones equivocadas. Por tanto, **`STROKE_MIN_CONFIDENCE=0.99` no
es una garantía de precisión** con este modelo; está sobreconfiado.

Cambios aplicados en el pipeline para que esto sea visible y decidible:

- `ml/metrics/pose_seq.json` incluye `threshold_target_precision`, `threshold_reached_target`,
  `threshold_used_fallback`, `val_precision_at_threshold` y `critical_confusion` (conteo/tasa de
  `other → serve`). `export.py` emite un warning cuando se usa el fallback.
- `python -m tennis_ml diagnose --out metrics/diagnostic.json` imprime F1 por clase, precisión por
  franja de confianza y la confusión crítica de val y test **sin re-entrenar**.
- `ml/params.yaml → pose_seq` añade `other_weight_boost`, `noise_std`, `time_warp` y
  `other_serve_penalty` (neutros por defecto) para barrer mejoras sin reescribir código. Los class
  weights inversos y la normalización por torso **ya existían**; no hay que reimplementarlos.

**Compuertas para activar clasificación automática** (además del macro-F1 y el F1 de `other`):

1. `critical_confusion.other→serve.count ≤ 1` sobre los 15 clips de smash en test.
2. `threshold_reached_target: true` (si es `false`, el umbral no significa lo que dice).
3. `accuracy_at_threshold ≥ 0.95` y `coverage_at_threshold ≥ 0.50`.

Si no se cumplen, se documenta el lanzamiento en **modo asistido** (Paso 4) o se sigue iterando;
no se activa la clasificación ciega aunque el macro-F1 suba.

#### 3.5. Resultado del barrido de hiperparámetros (2026-11-17)

Evidencia completa: `ml/metrics/sweep_results_20261117.json` y `ml/metrics/diagnostic.json`.
Ninguna variante se publicó ni sobrescribió `ml/artifacts/pose_seq/` ni `ml/metrics/pose_seq.json`.

| Variante | macro-F1 | F1 `other` | `other→serve` | Umbral | Cobertura | Precisión@umbral (test) |
|---|---|---|---|---|---|---|
| Baseline | 0.7594 | 0.6087 | 5/15 | 0.99 (fallback) | 0.70 | 0.8492 |
| Solo boost ×2 | 0.7007 | 0.4762 | 8/15 | 0.99 (fallback) | 0.46 | 0.8537 |
| **Boost ×2 + ruido 0.01 + warp 0.02 + penalización 0.5** | **0.7794** | **0.75** | **1/15** | 0.9992 (calibrado en val) | 0.23 | 0.8810 |
| Penalización 1.0 | 0.7285 | 0.7647 | 0/15 | 0.99 (fallback) | 0.31 | 0.8393 |
| Aumentación fuerte | 0.7279 | 0.80 | 0/15 | 0.99 (fallback) | 0.34 | 0.8548 |
| Boost ×1.5 + aumentación + penalización | 0.7619 | 0.9375 | 0/15 | 0.9995 (calibrado en val) | 0.16 | 0.8929 |

Conclusiones:

- **Sí mejoró el riesgo crítico:** el error smash→saque baja de 5/15 a 1/15 con la variante
  recomendada (y a 0/15 con boost ×1.5, a costa de cobertura).
- **No se publica:** macro-F1 0.7794 < 0.90 y la precisión sobre lo aceptado en test es 0.881 < 0.95
  aunque en validación el umbral sí alcance el objetivo (0.9505). Hay desplazamiento val→test y
  sobreconfianza residual.
- La cobertura cae al 23 %: el modo automático rechazaría 3 de cada 4 videos. En modo asistido
  eso es aceptable porque el usuario declara el golpe; en modo automático no.
- Se ha ampliado el grid de calibración por encima de 0.99 (`export.py:THRESHOLD_GRID`) porque el
  softmax satura: antes se forzaba el fallback aunque existiera un umbral válido.
- `ml/params.yaml` queda en la variante recomendada. Para reproducirla en Docker:
  `docker compose -f deploy/docker-compose.yml --profile ml run --rm ml dvc repro export_pose_seq`.
- **Siguiente decisión:** calibración por temperatura sobre pose_seq (ver §3.6: la ruta YOLO ya se
  probó y se descartó). Hasta entonces, worker en `STROKE_CLASSIFIERS=user` y Fase 2 puede arrancar
  con forehand declarado.

---

### 3.6. Resultado de la ruta YOLO (2026-11-17) — 🔴 descartada por ahora

Se entrenó **YOLO11n** sobre las cajas automáticas derivadas de la pose (8 fotogramas por clip,
11.232 imágenes de train y 3.168 de val), 50 épocas en GPU RTX 2080 Super (2,1 h). Evidencia:
`ml/metrics/yolo.json`, `ml/runs/detect/artifacts/yolo/run/results.csv` y `ml/export/yolo/`.

La evaluación no usa el mAP de detección de Ultralytics, sino **el `YoloFrameClassifier` que usa el
worker en producción**, con las mismas compuertas que `pose_seq` (val para calibrar, test para decidir):

| Modelo | macro-F1 | F1 `other` | `other→serve` | Umbral | Cobertura | Precisión@umbral (test) |
|---|---|---|---|---|---|---|
| pose_seq baseline | 0.7594 | 0.6087 | 5/15 | 0.99 (fallback) | 0.70 | 0.8492 |
| pose_seq recomendado (§3.5) | 0.7794 | 0.75 | 1/15 | 0.9992 (calibrado) | 0.23 | 0.8810 |
| **YOLO11n (este run)** | **0.6394** | **0.50** | **6/15** | 0.73 (calibrado en val) | 0.15 | 0.7407 |

Detalles del run YOLO: accuracy 0.6667 en test; F1 por clase 0.5854 forehand, 0.6939 backhand,
0.8276 serve, 0.5902 volley, 0.50 other. En validación el detector dio mAP50 0.578 (other 0.255),
y el umbral sí alcanzó el objetivo (precisión 0.9577 en val), pero en test cae a 0.7407: hay un
desplazamiento val→test aún mayor que en pose_seq.

**Decisión:** YOLO no se publica. Falla todas las compuertas del §3.4 salvo `threshold_reached_target`:
macro-F1 < 0.90, F1 `other` < 0.75, 6 errores smash→saque (límite 1), precisión@umbral 0.7407 < 0.95
y cobertura 0.15 < 0.50. Además es peor que pose_seq en todas las dimensiones, cuesta 2 h de GPU
y obligaría a publicar un modelo AGPL-3.0. El mejor pose_seq del barrido sigue siendo la referencia,
aunque tampoco alcanza la publicación; se mantiene `STROKE_CLASSIFIERS=user`.

**Incidencia resuelta:** la exportación de Ultralytics intentó autoinstalar `onnxslim`, que exige
numpy≥2 y rompió `onnxruntime` 1.18 (numpy<2) a mitad del proceso. El ONNX generado es válido: se
volvió a evaluar tras restaurar numpy 1.26.4 y dio métricas idénticas. `ml/src/tennis_ml/yolo.py`
pasa ahora `simplify=False` al exportar, con un test de regresión en `ml/tests/test_yolo_eval.py`,
para que futuros runs no degraden el entorno.

---

### 🚀 Paso 4: Publicar el modelo y activar la clasificación automática

**El worker no habla con MLflow, y es una decisión deliberada** (`worker/src/tennis_worker/infrastructure/model_store.py`). Al arrancar descarga una **versión fija** desde el bucket (`models/pose_seq/<versión>/model.onnx + meta.json`). Así el arranque es determinista y no depende de que el servidor de experimentos esté disponible. Por eso no conviene que el worker resuelva `models:/…@champion`: un alias mutable haría que dos reinicios del mismo despliegue pudieran cargar modelos distintos.

El flujo ya implementado:

1. **Publicar** la exportación validada:
   ```bash
   docker compose -f deploy/docker-compose.yml --profile ml run --rm ml python -m tennis_ml publish export/pose_seq
   ```
   Este comando sube `model.onnx` y `meta.json` al bucket (`S3_BUCKET`) y mueve el alias de MLflow de `stroke-pose-seq` a esa versión. El alias por defecto es `production`, no `champion`. En MLflow sirve de **trazabilidad** ("qué versión está en producción"), no como mecanismo de carga.
2. **Desplegar**: fija `POSE_SEQ_MODEL_VERSION=<versión>` (el comando anterior la imprime) en el entorno del worker y reinícialo. No hace falta reconstruir la imagen. Si la variable está vacía, el worker usa el modelo montado en `<stroke_models_dir>/pose_seq`.
3. **Umbral**: el worker usa `STROKE_MIN_CONFIDENCE` (0.7 por defecto). El `recommended_min_confidence` que la etapa de export calibra en validación y guarda en `meta.json` es **informativo**. Copia ese valor a `STROKE_MIN_CONFIDENCE` al desplegar.
4. **Activar la detección automática**: hoy el valor por defecto es `STROKE_CLASSIFIERS=user` (el golpe lo declara el usuario). Con `STROKE_CLASSIFIERS=user,pose_seq` y `STROKE_COMBINER=first`, se respeta el `stroke_type` cuando viene declarado y, si no, clasifica el modelo.

**Prueba de punta a punta:** sube un video real de un forehand **sin declarar `stroke_type`**. El worker debe clasificarlo, segmentar el golpe, calcular las métricas y devolver el reporte. Si la confianza no supera el umbral, o la clase es `other`, el análisis falla con `UNSUPPORTED_STROKE` y un mensaje accionable, como prevén los contratos.

**Criterio de salida:** un video sin `stroke_type` produce un reporte `completed` con el golpe correctamente clasificado y las métricas calculadas. Un smash produce `UNSUPPORTED_STROKE`.

---

### 📱 Paso 5: Probar la app Android en emulador o dispositivo

La app compila pero no se ha ejecutado contra el backend. Conéctala al backend local con:

```bash
adb reverse tcp:8080 tcp:8080
```

Ajusta el puerto si la API corre en otro. `adb reverse` es preferible a `10.0.2.2`: es explícito y funciona igual en dispositivo físico.

**Flujo a validar:** login por navegador (PKCE) → creación del análisis → subida directa al almacenamiento en *streaming* → *polling* de `GET /v1/analyses/{id}` hasta `completed` → visualización del reporte.

**Criterio de salida:** el flujo completo funciona en el emulador con `adb reverse` activo.

---

### 🍎 Paso 6: iOS — ⏸️ fuera de alcance en dev y staging

Decisión: en dev y staging solo Android. El código compartido ya declara los targets iOS. Faltan la UI en SwiftUI y el login con `ASWebAuthenticationSession`, que requieren macOS.

Cuando se retome (fuera de dev/staging), hay dos opciones, en orden de pragmatismo:
1. **Job en `macos-latest`** que compile el xcframework del módulo `shared` y, si existe, la app. No requiere un Mac físico; cuesta unas 10 veces más por minuto que Ubuntu.
2. **Mac local o en la nube** para probar la app en el simulador.

**Criterio de salida (cuando se retome):** el job de iOS compila `shared` en verde y, si la app está implementada, la construye.

---

### 🗺️ Mapa de culminación

```
[0] Sanear entorno            ✅ (VC++ · PATH)
        │
        ▼
[1] Primer commit + CI verde  ✅ (api, worker, ml, mobile)
        │
        ▼
[2] THETIS (✅ 1,980 clips / 55 jugadores)
    → docker compose ... run --rm ml dvc repro export_pose_seq
      (prepare → extract_poses → features → train → export) + dvc push
        │
        ▼
[3] Evaluar en test (5 jugadores, 180 clips, 5 clases):
    macro-F1 + F1/clase + matriz + tamaños de split + baseline
        │
        ▼
[4] tennis_ml publish → bucket + alias MLflow "production"
    → worker con POSE_SEQ_MODEL_VERSION fija + STROKE_CLASSIFIERS=user,pose_seq
        │
        ▼
[5] Android: adb reverse → flujo completo

    [6] iOS ⏸️ fuera de dev/staging
```

La diferencia con el plan anterior: **ya no hay un paso de "exportar poses" separado del de "entrenar"**, porque `dvc repro` hace ambos en una sola ejecución dentro de Docker. El éxito del Paso 3 tampoco es "198 clips con macro-F1 ≥ 0.90", sino **el split por jugador real (180 clips), con macro-F1 sobre 5 clases, F1 por clase, matriz de confusión y baseline, interpretado con honestidad respecto a la literatura.**
