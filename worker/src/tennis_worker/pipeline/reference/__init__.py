"""Comparación informativa con golpes de referencia (DTW sobre ángulos articulares).

No toca hallazgos ni puntos fuertes: el diagnóstico lo deciden las reglas expertas. Esto añade una
"similitud por fase" con un golpe modelo para orientar al jugador y al portal de expertos."""

from tennis_worker.pipeline.reference.comparator import ReferenceComparator, ReferenceProvider, ReferenceStroke
from tennis_worker.pipeline.reference.dtw import DtwResult, dtw

__all__ = ["DtwResult", "ReferenceComparator", "ReferenceProvider", "ReferenceStroke", "dtw"]
