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

## Métricas disponibles (forehand)

| id | Qué mide | Unidad |
| --- | --- | --- |
| `shoulder_rotation_range` | Cuánto giran los hombros desde la preparación hasta el impacto | grados |
| `hip_shoulder_separation_max` | Máxima diferencia de giro entre hombros y cadera antes del impacto | grados |
| `elbow_angle_at_impact` | Ángulo del codo del brazo dominante en el impacto (180 = recto) | grados |
| `knee_angle_min` | Ángulo mínimo de rodilla en la preparación (180 = pierna recta) | grados |
| `contact_point_ahead` | Cuánto por delante de la cadera está la muñeca en el impacto | metros |
| `follow_through_height` | Altura máxima de la muñeca sobre el hombro tras el impacto | metros |
| `kinetic_chain_sequence` | 1 si el orden es cadera → hombros → brazo; 0 si no | 0/1 |

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
   transacción se aplica el cambio, las reglas tocadas quedan `validated: true` y se publica una versión
   nueva (minor + 1). El worker la carga en ≤ `KNOWLEDGE_REFRESH_SECONDS` sin reiniciar.
4. Si una versión no valida en el worker, este la marca `invalid` y sigue con la anterior. Un validador o
   el admin puede reactivar cualquier versión válida (rollback).
5. Cada reporte guarda la versión usada (`knowledge_base_version`) y cuántas reglas están validadas
   (`knowledge_status`).

Los expertos **no analizan videos de usuarios a mano**: el análisis es siempre automático. Los videos de
usuarios que marcaron «Permitir que expertos vean este video» solo sirven para comprobar las reglas.

Para trabajar sin portal (desarrollo): `KNOWLEDGE_SOURCE=yaml` usa directamente los archivos. Si el
YAML está mal escrito o cita una métrica inexistente, el worker no arranca y explica el motivo.
