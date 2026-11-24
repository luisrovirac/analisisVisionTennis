# Banco de pruebas del segmentador

`scripts/bench_segmentation.py` mide el segmentador contra impactos anotados a mano. Las anotaciones son
las de [tennis-analyzer](https://github.com/rubenG1009/tennis-analyzer) (`references/golpes.csv`, licencia
MIT): 51 impactos en 11 clips de profesionales de YouTube, cada uno con su tolerancia en fotogramas.

Los clips **no se redistribuyen ni se usan como referencias del producto**. Solo sirven para evaluar y se
guardan en `data/bench/segmentation/` (fuera de git). Se bajan con el mismo formato y los mismos tramos que
el script original para que los números de fotograma coincidan.

```bash
python scripts/bench_segmentation.py download    # yt-dlp y ffmpeg en el PATH
python scripts/bench_segmentation.py extract     # pose cruda (MediaPipe full), ~6 min en CPU, una vez
python scripts/bench_segmentation.py evaluate -v  # segundos; compara variantes sobre las mismas poses
```

Las anotaciones se esperan en `../tennis-analyzer/references/` (clon del repositorio); se cambia con
`--annotations`.

## Qué mide

- **Exhaustividad / precisión:** una detección empareja con un impacto anotado si cae a menos de 0,5 s.
- **Impacto en tolerancia:** de los emparejados, cuántos caen dentro de la tolerancia de la anotación
  (±2 fotogramas en Sinner, verificado fotograma a fotograma; ±4–30 en el resto).
- **Elegido real:** si el único golpe que usa hoy el informe (`segment()`) es un golpe anotado.

## Resultados (2026-11-24, MediaPipe full, corrección del esqueleto activada, suavizado 0,1 s)

| Variante | Exhaustividad | Precisión | Impacto en tolerancia | Elegido real |
|---|---|---|---|---|
| `WristSpeedSegmenter` (anterior, 1 golpe por video) | 10 % (5/51) | 50 % | 5/5 | 5/11 |
| `PeakStrokeSegmenter` señal `world` | 94 % (48/51) | 55 % | 34/48 | 6/11 |
| **`PeakStrokeSegmenter` señal `image`** (por defecto) | 94 % (48/51) | **67 %** | **41/48** | 6/11 |
| señal `image` sin corrección del esqueleto | 94 % (48/51) | 60 % | 41/48 | 6/11 |

Solo video a velocidad real (Sinner 30 fps y Alcaraz 60 fps, 33 golpes de fondo), señal `image`:
**100 % encontrados, 85 % de precisión, impacto en tolerancia 30/33** (`world`: 24/33). En Sinner, 17 de
22 impactos caen a ±1 fotograma.

Saques (5, por la altura de la muñeca): 5/5 encontrados con las dos señales; en tolerancia 5/5 con `world`
y 4/5 con `image` (Zverev a +9 con tolerancia 8). Con tan pocos saques no se justifica una señal distinta
para el saque.

## Lo que queda abierto

- **Cámara lenta editada** (Djokovic a 1000 fps, compilaciones): el pico de velocidad cae en la
  terminación, no en el impacto, como ya documentaba tennis-analyzer. No afecta al video de móvil.
- **Elegir un golpe es lo más débil** (6/11): el pico más alto a veces es un artefacto del inicio del
  clip (un fundido desde negro en el revés de Djokovic, fotograma 7). Cuando el informe pase a ser por
  sesión (varios golpes), esta elección deja de ser necesaria.
- La señal `image` mide la proyección del movimiento en la imagen: con vista frontal o trasera el pico
  depende más del ángulo de la cámara. Los clips del banco son mayoritariamente laterales.
- 33 de los 51 golpes son de dos jugadores: conviene ampliar con golpes propios anotados (dataset `own`).
