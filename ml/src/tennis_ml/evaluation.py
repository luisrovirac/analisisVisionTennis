"""Métricas de evaluación que acompañan al macro-F1 en export_pose_seq.

El macro-F1 agregado no basta para decidir si se publica el modelo:
- el F1 por clase muestra si una clase concreta (p. ej. `other`, la minoritaria) se confunde sistemáticamente;
- los tamaños de split dicen cuánto pesa cada número (con pocos clips, el F1 de una clase es ruidoso);
- los baselines dan una referencia interna: el modelo debe superar con margen a algo simple.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from tennis_ml.datasets import Sample
from tennis_ml.features import SPLITS, Split

# Por debajo de este número de clips en test, el F1 de la clase se marca como poco fiable.
MIN_RELIABLE_SUPPORT = 20


def per_class_f1(y: NDArray[np.int64], predicted: NDArray[np.int64], classes: list[str]) -> dict[str, dict[str, Any]]:
    """F1 y soporte (clips reales) de cada clase en el split evaluado."""
    scores = f1_score(y, predicted, labels=range(len(classes)), average=None, zero_division=0)
    support = np.bincount(y, minlength=len(classes))
    return {c: {"f1": round(float(scores[i]), 4), "support": int(support[i])} for i, c in enumerate(classes)}


def low_support_classes(per_class: dict[str, dict[str, Any]], minimum: int = MIN_RELIABLE_SUPPORT) -> list[str]:
    return [c for c, v in per_class.items() if v["support"] < minimum]


def split_summary(samples: list[Sample], classes: list[str]) -> dict[str, dict[str, Any]]:
    """Jugadores, clips y distribución de clases por split (de labels.csv: la partición es por jugador)."""
    summary = {}
    for split in SPLITS:
        chosen = [s for s in samples if s.split == split]
        counts = Counter(s.label for s in chosen)
        summary[split] = {"players": len({s.player for s in chosen}), "clips": len(chosen),
                          "classes": {c: counts.get(c, 0) for c in classes}}
    return summary


def aggregate(x: NDArray[np.float32]) -> NDArray[np.float32]:
    """Secuencia (N, T, F) → vector por clip: media y desviación temporal de cada característica."""
    return np.concatenate([x.mean(axis=1), x.std(axis=1)], axis=1)


def baselines(splits: dict[str, Split], seed: int) -> dict[str, dict[str, float]]:
    """Referencias contra las que comparar el modelo, entrenadas en train y medidas en test.

    - majority: predice siempre la clase más frecuente en train (suelo trivial).
    - logreg: regresión logística sobre las características agregadas del clip (sin dinámica temporal).
    """
    train, test = splits["train"], splits["test"]
    if len(test.y) == 0 or len(train.y) == 0:
        return {}
    results = {"majority": _score(test.y, np.full_like(test.y, np.bincount(train.y).argmax()))}
    if len(np.unique(train.y)) >= 2:
        model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, random_state=seed))
        model.fit(aggregate(train.x), train.y)
        results["logreg"] = _score(test.y, model.predict(aggregate(test.x)))
    return results


def _score(y: NDArray[np.int64], predicted: NDArray[np.int64]) -> dict[str, float]:
    # Mismo cálculo que test_macro_f1 del modelo, para que la comparación sea directa.
    return {"macro_f1": round(float(f1_score(y, predicted, average="macro", zero_division=0)), 4),
            "accuracy": round(float((predicted == y).mean()), 4)}
