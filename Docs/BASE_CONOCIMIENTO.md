# Guía de la base de conocimiento (para entrenadores)

Cada golpe tiene un archivo en `worker/knowledge_base/<golpe>.yaml`. Cada **regla** dice: *si las
métricas cumplen estas condiciones, el jugador comete este error, y así se corrige*.

## Ejemplo

```yaml
- id: FH-SYNC-003                 # único; prefijo del golpe + tipo + número
  title: Golpeo con el brazo      # lo que ve el jugador
  category: sincronizacion        # fundamental | sincronizacion
  impact: 5                       # 1–5: cuánto mejora el golpe al corregirlo
  difficulty: 4                   # 1–5: cuánto cuesta corregirlo
  when:
    all:                          # all = deben cumplirse todas; any = basta con una
      - {metric: hip_shoulder_separation_max, op: lt, value: 20}
      - {metric: elbow_angle_at_impact, op: lt, value: 110}
  explanation: Qué está pasando y por qué es un problema.
  correction: Qué debe hacer el jugador.
  drills:
    - Ejercicio 1
```

Operadores: `lt` (<), `lte` (≤), `gt` (>), `gte` (≥), `eq` (=), `between` (`value: [min, max]`).

## Orden en que se muestran los errores

1. Primero los **fundamentales** (postura, preparación, punto de impacto), porque corregirlos
   facilita los de sincronización.
2. Después los de **sincronización** (cadena cinética).
3. Dentro de cada grupo: primero el de mayor `impact`; a igual impacto, el de menor `difficulty`.

## Métricas disponibles

Una regla solo puede usar las métricas de **su** golpe. Las define el worker en
`worker/src/tennis_worker/pipeline/biomechanics/registry.py`; el portal las muestra al crear una regla.

| id | Qué mide | Unidad | FH | Revés | Saque | Volea |
| --- | --- | --- | :-: | :-: | :-: | :-: |
| `shoulder_rotation_range` | Cuánto giran los hombros desde la preparación hasta el impacto | grados | ✓ | ✓ | ✓ | ✓ |
| `hip_shoulder_separation_max` | Máxima diferencia de giro entre hombros y cadera antes del impacto | grados | ✓ | ✓ | ✓ | |
| `elbow_angle_at_impact` | Ángulo del codo del brazo dominante en el impacto (180 = recto) | grados | ✓ | ✓ | ✓ | ✓ |
| `knee_angle_min` | Ángulo mínimo de rodilla en la preparación (180 = pierna recta) | grados | ✓ | ✓ | ✓ | ✓ |
| `contact_point_ahead` | Cuánto por delante de la cadera está la muñeca en el impacto | metros | ✓ | ✓ | | ✓ |
| `follow_through_height` | Altura máxima de la muñeca sobre el hombro tras el impacto | metros | ✓ | ✓ | | |
| `kinetic_chain_sequence` | 1 si el orden de los picos es cadera → hombros → brazo, tolerando ~34 ms (1 fotograma a 30 fps) de inversión; 0 si no | 0/1 | ✓ | ✓ | ✓ | |
| `stance_width_ratio` | Separación de los tobillos en el impacto / anchura de hombros | ratio | ✓ | ✓ | | ✓ |
| `head_stability` | Cuánto se mueve la cabeza (nariz) ±0,1 s alrededor del impacto, medido en la imagen y en largos de torso (menor = más quieta; supone cámara fija) | ratio | ✓ | ✓ | | ✓ |
| `racket_drop_below_contact` | Cuánto baja la muñeca por debajo de la altura del impacto antes de golpear (swing de abajo arriba) | metros | ✓ | ✓ | | |
| `leg_drive` | Extensión de rodilla desde su máxima flexión hasta el impacto (empuje de piernas) | grados | ✓ | ✓ | ✓ | |
| `unit_turn_lead_time` | Segundos entre completar el 80 % del giro de hombros y el impacto (mayor = preparación más temprana; máx. ≈ 1 s) | s | ✓ | ✓ | | |
| `off_arm_extension` | Cuánto se estira el brazo libre hacia delante en la preparación | metros | ✓ | | | |
| `wrist_separation_at_impact` | Distancia entre muñecas en el impacto (< 0,2 m ≈ revés a dos manos) | metros | | ✓ | | |
| `contact_height` | Altura de la muñeca sobre la cabeza en el impacto | metros | | | ✓ | |
| `trophy_elbow_angle` | Ángulo del codo de golpeo en la posición de trofeo (≈ 90° es lo habitual) | grados | | | ✓ | |
| `toss_arm_height` | Altura de la muñeca del brazo de lanzamiento sobre la cabeza en la posición de trofeo | metros | | | ✓ | |
| `shoulder_tilt_at_trophy` | Inclinación de hombros en la posición de trofeo (positivo = hombro de golpeo más bajo) | grados | | | ✓ | |
| `backswing_wrist_travel` | Recorrido de la muñeca en los 0,5 s antes del impacto (longitud del armado) | metros | | | | ✓ |
| `follow_through_length` | Recorrido de la muñeca en los 0,3 s después del impacto | metros | | | | ✓ |

Notas para interpretar los valores:

- La **posición de trofeo** del saque se detecta como el instante en que el brazo de lanzamiento está más
  alto.
- Las coordenadas que da MediaPipe están centradas en la cadera, así que **no se puede medir el
  desplazamiento del cuerpo por la pista** (p. ej. la transferencia de peso). En su lugar se usa
  `leg_drive`.
- Para distinguir el revés a una mano del de dos, combina la regla con `wrist_separation_at_impact`
  (ejemplo: `BH-ARM-001`).
- Para expresar «fuera del rango», usa `any` con dos condiciones (ejemplo: `SV-TROPHY-001`).

## Umbrales: cómo calibrarlos

Los umbrales de las semillas son **provisionales**. La literatura de biomecánica (normalmente captura 3D
en laboratorio) dice *qué* medir y *hacia dónde* está el error, pero los valores de una pose 2D estimada
desde un video no coinciden exactamente. Para ajustar un umbral:

1. Graba 20–30 golpes de referencia buenos (entrenador o jugador avanzado) y otros tantos de principiantes,
   con la cámara de lado y el cuerpo entero visible.
2. Compara los valores de la métrica en los dos grupos (aparecen en cada reporte) y coloca el umbral entre
   ambos.
3. En el portal, usa la **vista previa** de la propuesta: muestra en cuántos análisis reales se dispararía
   la regla antes y después del cambio.
4. Cuando haya ground truth de consenso (ver «Validación formal» más abajo), usa **Calibrar** en el
   editor de la regla: barre el umbral contra las calificaciones de los expertos en los casos de
   *calibración* y muestra precisión, recall y F1 para cada valor. Los casos de *test* nunca se usan
   para ajustar: sirven para validar sin sesgo.

Fuentes útiles para redactar reglas nuevas (campo `references`): *Biomechanics of Advanced Tennis*
(Elliott, Reid y Crespo, ITF, 2003); *Biomechanical Principles of Tennis Technique* (Knudson, 2006);
Kovacs y Ellenbecker, «An 8-stage model for evaluating the tennis serve» (*Sports Health*, 2011); la
revista gratuita *ITF Coaching & Sport Science Review*.

## Añadir una métrica nueva (programación)

1. Crea la clase en `worker/src/tennis_worker/pipeline/biomechanics/metrics.py` (id, label, unit, compute)
   y añádela a los golpes que la usen en `registry.py`, con su test en `worker/tests/`.
2. Regenera el catálogo de ejemplo: `worker/.venv/Scripts/python.exe scripts/export_metric_catalog.py`.
3. Reconstruye el worker. Al arrancar publica el catálogo en `metric_catalog` y el portal ya la ofrece.
4. Si la nueva métrica la usan reglas de una semilla YAML, sube la `version` del YAML. El worker publica
   la semilla nueva **solo** si ese golpe todavía no se ha tocado desde el portal (no hay propuestas ni
   versiones publicadas por expertos). En otro caso, las reglas nuevas se proponen desde el portal.

Las mediciones con poca confianza (partes del cuerpo poco visibles) **no disparan reglas**. En su
lugar, el jugador recibe un aviso para mejorar la grabación.

## Puntos fuertes y causa raíz

- `polarity: strength` marca un **punto fuerte**. No es la ausencia de un error: tiene su propio umbral
  (más exigente) y necesita `why_it_matters` (y opcionalmente `how_to_maintain`). Se informan como
  máximo 5, solo con métricas fiables, y nunca si aparece uno de los errores de `suppressed_by` (las
  reglas de salud mandan).
- `caused_by` (solo errores) enlaza un error con los errores que lo explican. El diagnóstico muestra la
  **causa raíz** (el error mejor priorizado que no es consecuencia de otro presente) y los síntomas que
  explica, y el plan de ejercicios empieza por ella.
- `references` guarda bibliografía o fuentes (p. ej. `PMID 24149800`).

## Flujo de validación (portal profesional)

El YAML es solo la **semilla**: la primera vez que arranca, el worker lo importa a Postgres como versión
activa (`kb_releases`) y publica su catálogo de métricas (`metric_catalog`). A partir de ahí la base se
gobierna desde el portal (`/portal.html`):

1. El **experto** propone cambios (crear, editar o retirar reglas), con motivo y evidencia. Puede
   previsualizar en cuántos análisis reales recientes se dispararía la regla antes y después.
2. Al enviarla, el sistema la valida contra el esquema, contra la base activa (ids, referencias) y
   contra el catálogo de métricas. Las métricas que aún no existen se piden en «Solicitudes de métricas».
3. Un **validador** distinto del autor aprueba, pide cambios o rechaza. Al aprobar, en una sola
   transacción se aplica el cambio, las reglas tocadas pasan a `expert_reviewed` (o a
   `empirically_validated`, ver abajo) con su traza (`validation`: quién y cuándo) y se publica una
   versión nueva con su **changelog** y salto **semver**: *patch* si solo cambian textos, ejercicios,
   referencias o evidencia; *minor* si cambian condiciones, umbrales o vistas, o se añade una regla;
   *major* si se retira una regla o cambia su polaridad. El worker la carga en ≤
   `KNOWLEDGE_REFRESH_SECONDS` sin reiniciar.
4. Si una versión no valida en el worker, este la marca `invalid` y sigue con la anterior. Un validador o
   el admin puede reactivar cualquier versión válida (rollback).
5. Cada reporte guarda la versión usada (`knowledge_base_version`) y cuántas reglas están validadas
   (`knowledge_status`).

Los expertos **no analizan videos de usuarios a mano**: el análisis es siempre automático. Los videos de
usuarios que marcaron «Permitir que expertos vean este video» solo sirven para comprobar las reglas.

## Validación formal de las reglas (revisiones, Delphi, ground truth)

Aprobar una regla significa que un validador revisó su lógica y su contenido; **no** significa que
acierte. Para eso hace falta compararla con el juicio de varios expertos sobre casos reales. Cada regla
tiene un `validation_status` (KB 2.1):

| Estado | Significado |
|---|---|
| `heuristic_unvalidated` | Escrita por alguien, sin revisión ni datos. Así entran las semillas YAML. |
| `expert_reviewed` | Un validador aprobó su lógica y su contenido. |
| `empirically_validated` | Además acierta contra el ground truth de consenso en el conjunto de **test**: n ≥ 30 casos, precisión y recall ≥ 0,8 y, si hubo campaña, α ≥ 0,667. |

El experto puede *pedir* `empirically_validated` en el editor; la API lo concede al aprobar solo si la
evidencia alcanza (si no, responde `422 VALIDATION_EVIDENCE_INSUFFICIENT`). Junto a la regla quedan
`evidence` (justificación), `evidence_level` (`provisional` | `literature` | `expert_validated`) y la
traza `validation` (ids de quien validó, fecha, n, precisión, recall, α). Nada de esto llega con nombres
al reporte del jugador; el reporte solo muestra `validation_status` por hallazgo (reporte 1.3).

### Escala de calificación

Para cada regla, el experto califica **el aspecto que evalúa** (no la regla) de 1 a 7: 1 muy
deficiente · 4 aceptable · 7 excelente, o marca «no evaluable» si la grabación no lo permite. Para un
error, una mediana ≤ 3 significa «el error está presente» y ≥ 5 «ausente»; en un punto fuerte, al
revés. El 4 y las medianas intermedias son indeterminados y no cuentan para precisión/recall.

### Revisión libre (pestaña «Casos»)

En cada caso, el experto ve el video, el esqueleto y lo que dijo el sistema, y por regla da su
calificación 1-7 y un veredicto: *acertó*, *falso positivo*, *se le escapó* o *no sé* (más el nivel del
jugador y notas). Ve el agregado anónimo de las revisiones de otros. Sirve como retroalimentación
continua y alimenta los KPIs; nadie revisa sus propios videos.

### Corpus y campañas Delphi (pestaña «Campañas y corpus»)

1. **Corpus.** Un validador o admin agrega casos al corpus desde «Casos». Cada caso cae de forma
   determinista en *calibración* (≈70 %) o *test* (≈30 %). Objetivo: 50-200 casos por golpe, con
   variedad de niveles y condiciones. Si el dueño retira el permiso de revisión, el caso deja de usarse.
2. **Campaña.** El coordinador elige casos del corpus, reglas y evaluadores (3-5 expertos o
   validadores) y fija **de antemano** la definición de consenso: por defecto, al menos el 70 % de las
   calificaciones a ±1 de la mediana, con al menos 3 evaluadores, y máximo 3 rondas.
3. **Ronda 1 (ciega).** Cada evaluador ve solo el video, el esqueleto y las fases: ni hallazgos ni
   métricas del sistema, para evitar el sesgo de anclaje. Califica cada aspecto de 1 a 7.
4. **Análisis.** El portal calcula por regla y ronda el **α de Krippendorff ordinal** (admite datos
   faltantes) y el % de casos con consenso. Interpretación: α ≥ 0,800 fiable; 0,667-0,800 solo para
   conclusiones tentativas; < 0,667 no sirve para calibrar (el aspecto está mal definido o no se ve
   bien en 2D: conviene redefinir el criterio o la grabación).
5. **Ronda 2+ (Delphi).** Cada evaluador ve, por aspecto, la distribución anónima del grupo (mediana,
   Q1-Q3, recuento por nota) y su propia nota anterior, y puede revisarla. Durante la ronda, los
   evaluadores no ven las estadísticas en curso; el coordinador sí.
6. **Cierre.** Las medianas de la última ronda pasan a ser el **ground truth** de cada caso y regla
   (con su etiqueta y si hubo consenso). Una campaña posterior sobre el mismo caso y regla lo reemplaza.

### Calidad de la base (pestaña «Calidad de la base»)

KPIs por golpe: % de reglas revisadas y validadas con datos, α medio, acierto global en test (con
intervalo de Wilson al 95 %), tamaño del corpus y del ground truth. Por regla: estado, α, precisión y
recall en test, tasa de «medible» en los análisis recientes (para distinguir una regla mala de una
métrica ruidosa o una vista equivocada) y veredictos de las revisiones libres. Se marcan para
**auditoría** las reglas nunca revisadas, sin revisiones en 90 días, con precisión o recall < 0,8 (con
≥ 10 casos), con α < 0,667, medibles en menos de la mitad de los análisis, o marcadas como validadas
con datos cuya evidencia actual ya no alcanza. Revisión recomendada: **trimestral**, con el panel de
expertos, incorporando literatura nueva y lo observado en los datos.

### Fundamentos

- Krippendorff, K. *Content Analysis: An Introduction to Its Methodology* (umbrales de α) y «Computing
  Krippendorff's Alpha-Reliability» (2011): su ejemplo publicado verifica el cálculo en los tests.
- Diamond, I. R. et al. (2014). «Defining consensus: a systematic review recommends methodologic
  criteria for reporting of Delphi studies». *J Clin Epidemiol* 67(4):401-409 (definir el consenso
  antes de empezar).
- Koo, T. K. y Li, M. Y. (2016). «A guideline of selecting and reporting intraclass correlation
  coefficients for reliability research». *J Chiropr Med* 15(2):155-163 (alternativa ICC).
- Wilson, E. B. (1927). Intervalo de confianza para proporciones (precisión, recall y acierto).
- Estudios recientes sobre la fiabilidad del juicio de entrenadores con video 2D y sobre la validez
  desigual de los criterios cinemáticos inspiraron el diseño; no se usan como respaldo normativo de
  ningún umbral.

Para trabajar sin portal (desarrollo): `KNOWLEDGE_SOURCE=yaml` usa directamente los archivos. Si el
YAML está mal escrito o cita una métrica inexistente, el worker no arranca y explica el motivo.

## Cómo se protege la medición (fase 1)

Para que un video mal grabado no produzca un diagnóstico falso, el worker:

1. **Suaviza** las trayectorias de la pose (Savitzky-Golay, 0,1 s, mínimo 5 fotogramas) para quitar el
   temblor del estimador sin aplanar los picos.
2. **Fija el impacto con el sonido** del golpe: busca un chasquido agudo cerca del pico de velocidad de la
   muñeca y solo lo acepta si sobresale 10 dB o más del ruido de fondo. El reporte dice en «Fiabilidad»
   si el impacto se confirmó por el sonido o se estimó por la muñeca.
3. **Prueba la sensibilidad** de cada métrica: la recalcula moviendo el impacto dentro de su incertidumbre
   (±1 fotograma con audio; ±0,05 s sin él). Si el valor cambia más que la tolerancia de su unidad
   (15°, 0,05 m, 0,15 en cocientes, 0,1 s), pierde confianza y deja de disparar reglas. El valor usado es la
   mediana alrededor del impacto.
4. **Respeta la vista de cámara**: una regla puede declarar `views: [side]` o `views: [front, back]`. Fuera
   de esas vistas no se evalúa y el reporte avisa de qué vista grabar para completarla. Las semillas
   marcan como solo de perfil las reglas del punto de impacto, del swing de abajo arriba y de las longitudes
   de la volea, y como solo frontal o trasera las de anchura de la base e inclinación de hombros.
5. **Avisa de problemas de grabación**: menos de 50 fps, jugador pequeño en la imagen, cabeza o pies
   fuera del encuadre.

Consecuencia práctica: con videos a 30 fps varias métricas que dependen del instante del impacto (giro
de hombros, cadena cinética, punto de impacto) suelen salir «no fiables». Es el comportamiento correcto:
la solución es grabar a 60 fps o más.

## Comparar estimadores de pose (fase 2)

Además de MediaPipe, el worker puede estimar la pose con **RTMPose** (2D, OpenMMLab) + **MotionBERT**
(3D a partir de la secuencia completa). La idea es que MotionBERT, al ver el movimiento entero, dé una
profundidad más estable que MediaPipe, que la estima fotograma a fotograma. **Solo se adopta si mejora
sobre el conjunto de referencia**; mientras tanto, MediaPipe sigue por defecto.

1. Exportar MotionBERT a ONNX y publicarlo en MinIO (una vez):
   `docker compose -f deploy/docker-compose.yml --profile ml run --rm ml python -m tennis_ml motionbert --publish`
   (descarga el checkpoint oficial fijado a una revisión, comprueba ONNX frente a PyTorch e imprime la
   versión, p. ej. `mb-lite-h36m-global-5cd873a3`).
2. Comparar sobre tus videos de referencia (cada video pasa por todo el pipeline con ambos estimadores):
   `worker/.venv/Scripts/python.exe scripts/compare_pose.py samples/*.mp4 --stroke forehand --motionbert ml/export/motionbert/model.onnx`
   Mira «métricas estables (solo tiempo)»: no depende de cómo calibra cada modelo su confianza. Compara
   también los valores con el criterio del entrenador en esos mismos videos.
3. Si gana, en el `.env`: `POSE_ESTIMATOR=rtmpose_motionbert` y `MOTIONBERT_MODEL_VERSION=<versión>`, y
   reconstruye el worker. Los modelos de RTMPose se descargan solos la primera vez (verificados por SHA-256).

Primera comparación (20-11-2026, un único video de forehand a 30 fps con audio): empate práctico. Hubo
9 de 13 métricas estables con MediaPipe y 8 de 13 con RTMPose + MotionBERT, cada uno mejor en métricas
distintas, y RTMPose + MotionBERT tardó unas 3 veces más. Con un solo video no se puede decidir.

Licencias: RTMPose y YOLOX (OpenMMLab) y MotionBERT son Apache-2.0; la licencia de MotionBERT se guarda junto
al modelo exportado (`LICENSE-MotionBERT`). Ideas de la fase 1 (impacto por audio, sensibilidad
temporal) inspiradas en repositorios MIT citados en `Docs/inspiracion02.md`; la implementación es propia.

## Visor de esqueleto y comparación con referentes

**Visor.** La web del jugador, la pestaña «Casos» del portal y la app Android muestran el video con el
esqueleto superpuesto (datos de `poses/<id>.json`) y las marcas de fase (inicio, armado, impacto, final).
Con «Ver en el video», en un error o punto fuerte, se salta al impacto y se resaltan las articulaciones de
las métricas que lo sustentan (en rojo los errores, en verde los puntos fuertes). El trazo tenue indica
que el estimador vio esa articulación con poca seguridad (visibilidad < 0,5, el mismo umbral que
`MIN_METRIC_CONFIDENCE`), y el gris, que el fotograma se interpoló porque no hubo detección.

**Casos (portal).** Son los análisis cuyo dueño marcó «Permitir que expertos vean este video». Se pueden
filtrar por golpe y por regla (desde una regla: «Ver casos donde dispara»), para comprobar sobre el video
si la regla acierta antes de proponer un cambio. Si el dueño retira el permiso, el caso desaparece. El portal
nunca muestra quién es el jugador.

**Comparación con referentes (DTW).** Es una métrica **informativa**: no genera errores ni puntos fuertes,
porque el diagnóstico lo siguen decidiendo las reglas. Funciona así:

1. Por cada fotograma se calculan 9 ángulos (codos, hombro dominante, rodillas, giro de hombros y de caderas
   respecto al impacto, separación cadera-hombro e inclinación del tronco). Así la comparación no depende de
   la estatura, la posición en la imagen ni la orientación de la cámara, y un zurdo se compara bien con una
   referencia diestra.
2. Cada fase (preparación, aceleración, terminación) se alinea en el tiempo con la de la referencia mediante
   *Dynamic Time Warping*, que tolera que un golpe sea más lento que otro. La similitud es
   `100 · e^(−desviación media / 20°)`, con la escala ajustable en `REFERENCE_SIMILARITY_SCALE_DEG`.
3. Se usa la referencia más parecida del mismo golpe y la misma vista de cámara.
4. **Puerta de evidencia:** una fase en la que el jugador se vio con visibilidad media < `MIN_METRIC_CONFIDENCE`
   no se puntúa (`low_confidence`). Una fase casi sin fotogramas (por ejemplo, sin armado visible) se marca
   `too_short`. Sin referencias registradas, el reporte dice `no_references` y no muestra ningún número.

**Registrar referencias** (de 3 a 5 golpes de calidad por golpe y vista, grabados según el protocolo y **con
permiso de uso**: el campo `--source` deja constancia):

```
worker/.venv/Scripts/python.exe scripts/register_reference.py video.mp4 --stroke forehand --view side \
    --handedness right --ref-id club-fh-side-1 --label "Drive de perfil (entrenador del club)" \
    --source "Grabación propia, cesión firmada"
```

El script pasa el video por el mismo pipeline de pose y rechaza grabaciones poco fiables: detección < 90 % o
alguna fase con visibilidad < 0,6. La detección se mide por defecto **solo en los fotogramas del golpe**
(`--detection-scope stroke`), que son los únicos que usa la comparación: un clip corto en el que el jugador
entra o sale de cuadro no se rechaza por eso. `--detection-scope clip` recupera el criterio estricto (todo el
video). Los umbrales se cambian con `--min-detection-rate` y `--min-phase-visibility`, o con
`REFERENCE_DETECTION_SCOPE`, `REFERENCE_MIN_DETECTION_RATE` y `REFERENCE_MIN_PHASE_VISIBILITY` en `.env`. Para
probar otro criterio, añade una función a `DETECTION_SCOPES` en
`worker/src/tennis_worker/pipeline/reference/acceptance.py` y el script la ofrece sola. `--impact-frame N` fija
el impacto a mano (fotograma N del clip, contando desde 0, verificado a ojo) en lugar de detectarlo por la muñeca y el
audio: úsalo cuando el audio tenga golpes de pistas vecinas, porque una referencia con el impacto mal puesto
desalinea las fases. En `manifest.json` es el campo opcional `impact_frame`. `--dry-run` analiza sin
subir nada. El worker relee el índice cada
`REFERENCE_REFRESH_SECONDS` (300 s por defecto). Registrar de nuevo el mismo `--ref-id` lo reemplaza. Para
desactivar la comparación: `REFERENCE_COMPARISON_ENABLED=false`.

Prueba local (22-11-2026): `samples/forehand.mp4` registrado como referencia y vuelto a analizar dio una
similitud de 98,4 (99,1 / 99,4 / 96,8 por fase). No da exactamente 100 porque MediaPipe no es determinista
entre ejecuciones. La referencia de prueba se retiró después.

**Referencias registradas (22-11-2026): derecha, vista lateral.** Cinco clips del *Tennis Shot Side-View and
Top-View Data Set* (Lai et al., Mendeley Data v4, doi:10.17632/75m8vz7jr2.4, **CC BY 4.0**: hay que citar la
fuente), carpeta `Indoor Field/Straight Shot/Side-View`: `ISS01`, `ISS03`, `ISS06`, `ISS10` e `ISS14`, como
`mendeley-fh-side-01` … `05`. Son 1080p a 60 fps, de un jugador diestro de nivel no declarado, con la cámara
algo oblicua. Los clips de exterior del mismo dataset no sirven: el jugador sale demasiado pequeño (detección
≈ 7 %). Conviene sustituirlas por golpes de un entrenador cuando los haya. Con `samples/forehand.mp4` (otro
jugador, 30 fps) la similitud fue 23,6 (32,5 / 28,4 / 9,8 por fase); la escala `REFERENCE_SIMILARITY_SCALE_DEG`
está sin calibrar.

Al registrarlas apareció un error: al leer una referencia del bucket, sus fotogramas sin detección volvían
como NaN, y `np.unwrap` (en `geometry.horizontal_yaw`) los propagaba a todo el giro de hombros y caderas.
Resultado: «la banda no permite ningún camino de alineación» y el reporte salía sin comparación. Corregido:
la referencia se vuelve a interpolar al cargarla, `horizontal_yaw` deja los NaN como locales y el DTW rechaza
los NaN con un mensaje claro.

**Contraste con profesionales (23-11-2026).** 21 derechas de profesionales en entrenamiento (Joao Fonseca y
otro jugador, Indian Wells 2025), recortadas de vídeos de *TK Tennis* en YouTube (**CC BY**, verificado con
yt-dlp), pasadas por las métricas y las reglas de `forehand.yaml` sin filtrar por vista:

| Regla | Disparos en profesionales | Diagnóstico |
| --- | --- | --- |
| `FH-SYNC-002` (cadena cinética desordenada) | 6/21 → **2/21** | Falso positivo por resolución temporal: los picos de cadera y hombros caen en el mismo fotograma o se invierten por uno. `kinetic_chain_sequence` tolera ahora 1 fotograma a 30 fps (2 a 60 fps). Afecta también a revés y saque. |
| `FH-HEAD-001` (cabeza se mueve, `head_stability > 0.25`) | 2/21 | El p90 de los profesionales es 0,24, justo al borde del umbral. Habría que subirlo a ~0,3 desde el portal si los principiantes lo confirman. |
| `FH-LEG-001` | 1/21 | Aceptable. |

Medianas en profesionales: `shoulder_rotation_range` 91°, `hip_shoulder_separation_max` 28°, `knee_angle_min`
105°, `contact_point_ahead` 0,49 m, `unit_turn_lead_time` 0,70 s. No sirve para calibrar umbrales sin la
contraparte de principiantes; solo detecta reglas que castigan golpes buenos.

**Referencias registradas (23-11-2026):** 5 derechas `front` (3/4 frontal), 1 revés a dos manos `front` y
2 saques `side`, de los mismos vídeos CC BY. Los clips, `manifest.json` (con el origen de cada uno) y
`register_all.py` están en `videos-tennis/creative-commons/referencias/`.

**Vista trasera, registradas (23-11-2026):** 4 saques (`tk-sv-back-01…04`), 5 reveses a dos manos
(`tk-bh-back-01…05`) y 5 derechas (`tk-fh-back-01…05`) de Alexander Zverev, diestro, entrenando en Indian Wells.
Están recortados de los exports de CapCut `1123(6)`, `1123(7)` y `1123(8)` del vídeo de *TK Tennis*
https://www.youtube.com/watch?v=PQuhn47iFhA (**CC BY**, verificado con yt-dlp). Son 1080p a 30 fps, con el
contenido vertical entre bandas negras. En estos clips el impacto por audio falló en 10 de 20: caía 6-12 fotogramas
tarde, en la terminación, o en otro golpe, por el sonido de las pistas vecinas. Por eso todos se registraron con
`impact_frame` verificado a ojo. El contacto real cae unos 3 fotogramas antes del pico de velocidad de la muñeca.
El vídeo original cambia de zoom a mitad de algunos golpes. No afecta a la comparación DTW, que usa ángulos, pero
estos clips no sirven para métricas en píxeles.

**Revés lateral, registradas (23-11-2026):** `tk-bh-side-01…04`, reveses a dos manos de dos jugadores diestros:
Andrey Rublev (2, de https://www.youtube.com/shorts/WqlsO2qNoEc) y Sebastian Korda (2, de
https://www.youtube.com/shorts/oYEiJujy0Q4). Ambos son Shorts CC BY de *TK Tennis*, verticales, a 30 fps. Se
registraron con `impact_frame` verificado a ojo. El Short https://www.youtube.com/shorts/FRnT5dVYUQI (Zhang) **no**
se usó: tiene la licencia estándar de YouTube, no CC.

**Saque frontal, registradas (23-11-2026):** `tm-sv-front-01…02`, dos saques de un entrenador diestro en
3/4 frontal, recortados del export CapCut `1123(12)` del vídeo de *The Tennis Mentor*
https://www.youtube.com/watch?v=OYQ31ZVv1bY (**CC BY**, verificado con yt-dlp). El vídeo trata de un error del
saque y su corrección; según quien eligió los fragmentos, en estos dos el saque está bien hecho. Como hay clips de
más de un canal, `manifest.json` acepta ahora un campo `channel` por clip. El export `1123(13)`, un profesional
en tierra batida de Roland Garros incluido en ese mismo vídeo, **no** se registró: es metraje de torneo cuyo autor
no consta, y la licencia CC BY del canal solo cubre lo que el canal grabó.
