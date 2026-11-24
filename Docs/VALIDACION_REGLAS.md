# Validación de las reglas con profesionales

`scripts/validate_rules.py` pasa golpes de profesionales por el pipeline real (corrección del esqueleto,
suavizado, métricas con prueba de sensibilidad) y por las reglas de `worker/knowledge_base/*.yaml`. Un
profesional no debería disparar reglas de error. Si una regla salta con él, su umbral marca como defecto algo
que hace un buen jugador.

**Esto no calibra un umbral.** Falta el otro lado, los principiantes (ver «Umbrales: cómo calibrarlos» en
[BASE_CONOCIMIENTO.md](BASE_CONOCIMIENTO.md)). Lo que sí da es una **cota**: hasta dónde puede llegar el umbral
sin castigar a profesionales.

```bash
python scripts/validate_rules.py extract            # baja los clips que falten y extrae la pose, una vez
python scripts/validate_rules.py evaluate [--metrics]  # unos segundos
```

## Método

El método es el de `validar_comparacion.py` de [tennis-analyzer](https://github.com/rubenG1009/tennis-analyzer)
(MIT), con implementación propia en `worker/src/tennis_worker/pipeline/knowledge/pro_validation.py`, que tiene
tests.

- **Sesión.** Cada jugador, por golpe y vista, se resume con la mediana de cada métrica sobre sus golpes. Solo
  cuentan los valores fiables (confianza ≥ 0,5), y la métrica tiene que ser fiable en al menos la mitad de
  los golpes.
- **Cota.** Para un defecto «por debajo» (`lt`), la cota es la menor mediana de los jugadores menos 0,25 veces
  la dispersión p10–p90 típica de un jugador. Para un defecto «por encima» (`gt`), es simétrica. Hacen falta
  al menos 3 jugadores. Un umbral que pasa la cota (✗) disparará con algún profesional.
- **Dejando fuera a cada jugador.** Se recalcula la cota sin un jugador y se mira si ese jugador queda del lado
  bueno. Así se mide cuántas falsas alarmas cabe esperar con un profesional que no está en los datos. Hacen
  falta 4 jugadores.
- **Condiciones combinadas.** En una regla `all` con varias condiciones, una de ellas puede ser solo una
  puerta. Por ejemplo, `FH-LEG-001` dice «rodilla flexionada **y** sin empuje»: que los profesionales
  flexionen la rodilla no castiga a nadie. Esas condiciones salen con «·» y se juzgan por los disparos de la
  regla entera.

## Golpes (2026-11-24)

- **tennis-analyzer:** `references/golpes.csv`, anotados a mano, y `golpes_auto.csv`, con impacto automático y
  tipo y vista verificados a ojo. La vista es la del cuerpo en el impacto. Los clips se usan solo para evaluar
  y se guardan en `data/` (fuera de git).
- **Referencias CC BY** de `videos-tennis/creative-commons/referencias/`. Unos clips llevan el impacto
  verificado a ojo; en el resto, el impacto lo pone el segmentador más el audio, como en producción.
- **Cámara lenta, fuera.** Las métricas con ventanas en segundos no valen en cámara lenta editada. Se quitan
  los clips de 1000 fps, la compilación y la derecha de Roddick. Además se quita todo golpe cuya muñeca vaya
  a menos de 6 torsos/s en el impacto. El vídeo a velocidad real da 8–22; Dimitrov, Kyrgios y los saques de
  Federer, Osaka y Zverev (`sv_zverev_lat`) dan 1,8–4,3.

En total quedan 147 golpes. Cuando el jugador no tiene nombre, la etiqueta es el vídeo de origen.

| Golpe / vista | Jugadores (golpes) |
|---|---|
| derecha / lateral | Djokovic 26, Nava 16, Lehecka 14, Sinner 11, Ymer 9, Alcaraz 5 → **dejando uno fuera** |
| derecha / frontal | Alcaraz 11, Fonseca 3, vídeo BYkiqxw1QwY 2 → solo cota |
| revés / lateral | Sinner 11, Nava 6, Rublev 2, Korda 2, Alcaraz 1 → **dejando uno fuera** |
| saque / lateral | Alcaraz 6, Serena 1, vídeo GjhPsm_vLO0 1, vídeo jvkUTVDGiCo 1 → **dejando uno fuera** |
| vista trasera; revés y saque frontales | 1–2 jugadores: no hay cota |

## Resultados

**Dejando fuera a cada jugador, el profesional queda dentro de la cota en el 90 % de los casos (72/80).**
tennis-analyzer obtenía cerca del 88 %. Una cota calculada con 4–6 profesionales generaliza razonablemente
a otro profesional, pero no del todo.

**Umbrales que castigan a profesionales (✗):**

| Regla | Condición | Cota | Profesionales que la disparan (sesión) | Lectura |
|---|---|---|---|---|
| `FH-HEAD-001` | `head_stability > 0.25` | 0,32 | Sinner (0,29), Lehecka; 21 % de los golpes sueltos | Confirma el contraste del 23-11 (p90 = 0,24). La métrica supone cámara fija, y algunos clips siguen al jugador, lo que la infla. |
| `BH-HEAD-001` | `head_stability > 0.25` | 0,30 | Rublev (0,27, 2 golpes) | Igual. |
| `SV-TROPHY-001` | `trophy_elbow_angle > 130` | 134,7 | Zverev en vista trasera (141,9), Serena en el límite (129,7, 1 golpe) | Hay pocos saques. Con la cámara detrás, el codo se ve más abierto. |
| `BH-FIN-001` | `follow_through_height < 0.0` | −0,02 | ninguna sesión; 18 % de los golpes sueltos | Sinner termina el revés bajo (0,03). Está en el límite. |
| `BH-SYNC-001` | `hip_shoulder_separation_max < 15` | 8,45 | Alcaraz (10,7) | Es un solo golpe: dato débil. |

**Reglas sin cota que también saltan:** `SV-SYNC-001` y `BH-SYNC-002` (`kinetic_chain_sequence = 0`, cadena
desordenada) saltan en 2 de 6 sesiones de saque y en Zverev de espaldas. `SV-ARM-001` (`elbow_angle_at_impact < 145`)
salta en el entrenador del saque frontal. Es la misma resolución temporal que ya obligó a dar tolerancia a la
cadena cinética en la derecha. A 30 fps, el orden de los picos del saque no es fiable.

**Umbrales con mucha holgura** (lejos de la cota; se podrían endurecer, pero solo con datos de
principiantes): `FH-PREP-001` (< 45° frente a una cota de 65°), `BH-PREP-001` (< 45° frente a 72°),
`FH-POST-001` (> 160° frente a 130°), `FH-ARM-002` (< 0,15 frente a 0,38 m), `SV-TOSS-001` (< 0,25 frente a
0,40 m) y `SV-TROPHY-001` por debajo (< 70° frente a 107°).

**Por sesión frente a por golpe.** Sumando todas las reglas de error, saltan en el 7,0 % de las evaluaciones
por golpe suelto (67/952) y en el 6,0 % por sesión (11/183). La sesión elimina los disparos que dependen de un
golpe ruidoso: `FH-SYNC-001` pasa del 9 % al 0 %, `BH-FIN-001` y `BH-LEG-001` del 18 % al 0 %, y `FH-FIN-001`
del 5 % al 0 %. Lo que queda es estilo del jugador o un umbral estricto (la cabeza). Es otro argumento para
el informe por sesión.

**Métricas que casi nunca son fiables a 30 fps** (porcentaje de golpes de derecha con confianza ≥ 0,5):
`contact_point_ahead` 2 %, `racket_drop_below_contact` 4 %, `unit_turn_lead_time` 10 %,
`elbow_angle_at_impact` 13 % y `stance_width_ratio` 13 %. Sus reglas (`FH-CONT-001`, `FH-SWING-001`,
`FH-PREP-002`, `FH-ARM-001`, `FH-BASE-001`…) **casi nunca se pueden evaluar** con vídeo normal. Por eso no se
pueden validar aquí, y el jugador tampoco las recibe. Coincide con tennis-analyzer: las métricas del brazo
necesitan cámara lenta (120–240 fps).

## Cambios aplicados a la semilla (2026-11-24)

Las tablas de arriba son de antes de estos cambios.

| Semilla | Cambio |
|---|---|
| `forehand.yaml` 0.4.0 | `FH-HEAD-001`: `head_stability > 0.25` → `> 0.35` |
| `backhand.yaml` 0.2.0 | `BH-HEAD-001`: igual. `BH-SYNC-002` solo con vista `side` o `front` |
| `serve.yaml` 1.0.0 | `SV-TROPHY-001`: límite superior 130° → 140°, solo con vista `side` o `front`. **`SV-SYNC-001` retirada** (major) |

Después de los cambios, las reglas de error saltan en el 4,2 % de las evaluaciones por golpe (39/928) y en el
2,3 % por sesión (4/174). Antes eran el 7,0 % y el 6,0 %. Quedan cuatro disparos por sesión, todos de un solo
jugador y con pocos golpes, así que no justifican cambiar más umbrales:

- `FH-LEG-001`: vídeo BYkiqxw1QwY de frente, 2 golpes.
- `BH-SYNC-001`: Alcaraz, 1 revés.
- `SV-ARM-001`: entrenador, saque frontal, 2 golpes. El codo en el impacto es poco fiable a 30 fps.
- `SV-SYNC-002`: Alcaraz de espaldas, 1 saque.

El worker solo instala una semilla nueva si ese golpe no se ha tocado desde el portal. En otro caso, estos
cambios hay que proponerlos desde el portal. `SV-SYNC-001` se puede volver a proponer cuando haya vídeo en
cámara lenta.

Pendiente: pedir cámara lenta en el protocolo de grabación cuando el golpe tenga reglas de brazo o de
contacto.

## Lo que queda abierto

- **Faltan principiantes.** Las cotas dicen hasta dónde se puede mover un umbral, no dónde ponerlo.
- **Faltan jugadores.** Con 1 golpe (Alcaraz en el revés, Serena) la mediana es un golpe suelto. Faltan vista
  trasera, saque frontal y revés a una mano.
- **Cámara que sigue al jugador.** Muchos clips de profesionales la tienen, y infla `head_stability`. En vídeo
  de móvil con trípode no pasa.
