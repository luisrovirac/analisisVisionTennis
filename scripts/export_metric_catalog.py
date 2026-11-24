"""Regenera contracts/fixtures/metric_catalog.example.json desde el registro de métricas del worker.

Uso (desde la raíz del repo): worker/.venv/Scripts/python.exe scripts/export_metric_catalog.py
Hazlo cada vez que añadas o renombres una métrica: los tests del worker y de la API comparan el fixture
con lo que el worker publica en metric_catalog.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker" / "src"))

from tennis_worker.pipeline.biomechanics.registry import default_registry  # noqa: E402


def main() -> None:
    rows = [{"stroke": e.stroke, "id": e.metric_id, "label": e.label, "unit": e.unit}
            for e in default_registry().catalog()]
    target = ROOT / "contracts" / "fixtures" / "metric_catalog.example.json"
    with target.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    print(f"{len(rows)} metricas -> {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
