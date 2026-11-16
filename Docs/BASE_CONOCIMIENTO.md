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

## Flujo de validación

1. Ajusta los umbrales con videos reales (`pytest -m integration -s` imprime las métricas de
   `samples/forehand.mp4`).
2. Cuando un entrenador revise el archivo, rellena `validated_by` con su nombre y sube `version`
   (por ejemplo `0.1.0 → 0.2.0`). Cada reporte guarda la versión usada (`knowledge_base_version`).
3. Si el YAML está mal escrito o cita una métrica inexistente, el worker no arranca y explica el motivo.
