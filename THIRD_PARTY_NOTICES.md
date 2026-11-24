# Avisos de terceros

## tennis-analyzer

Portado o adaptado de https://github.com/rubenG1009/tennis-analyzer:

- `worker/src/tennis_worker/pipeline/pose/skeleton_correction.py`, de `scripts/corregir_esqueleto.py` y
  `scripts/evaluar_esqueleto.py`.
- `PeakStrokeSegmenter` en `worker/src/tennis_worker/pipeline/segmentation.py`, de `scripts/segmentar_golpes.py`.
- `scripts/bench_segmentation.py` usa sus anotaciones `references/golpes.csv` y sigue su
  `scripts/evaluar_segmentacion.py`.
- `worker/src/tennis_worker/pipeline/knowledge/pro_validation.py` y `scripts/validate_rules.py`: el método de
  `scripts/validar_comparacion.py` (sesión = mediana por jugador, cota de las medianas ± 0,25 × dispersión,
  mínimo 3 jugadores, dejando fuera a cada jugador), con implementación propia. Usa sus anotaciones
  `references/golpes.csv`, `references/golpes_auto.csv` y `references/manifest.csv`.

Distribuido bajo esta licencia:

```
MIT License

Copyright (c) 2026 Rubén (rubenG1009)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
