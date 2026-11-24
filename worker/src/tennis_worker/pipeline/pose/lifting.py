"""Lifting 2D → 3D con MotionBERT (Zhu et al., ICCV 2023; Apache-2.0) sobre ONNX Runtime.

A diferencia de MediaPipe, que estima la profundidad fotograma a fotograma, MotionBERT usa la secuencia
completa (clips de hasta 243 fotogramas): la profundidad sale más estable porque el modelo "ve" el movimiento.

Replica la inferencia oficial `infer_wild.py` en modo --pixel con el modelo MB_ft_h36m_global_lite:
- entrada: 17 puntos Human3.6M (x, y, confianza), con x, y centrados en la imagen y divididos por min(w, h)/2;
- aumento por espejo (flip) y media de ambas predicciones;
- salida: coordenadas de cámara en las mismas unidades (y hacia abajo, z alejándose de la cámara).
El modelo ONNX lo genera ml/src/tennis_ml/motionbert_export.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tennis_worker.pipeline.pose.rtmpose import InferenceSession
from tennis_worker.pipeline.types import FloatArray

# Human3.6M: 0 pelvis, 1-3 cadera/rodilla/tobillo der., 4-6 izq., 7 columna, 8 tórax, 9 nariz, 10 cabeza,
# 11-13 hombro/codo/muñeca izq., 14-16 der.
H36M_LEFT = (4, 5, 6, 11, 12, 13)
H36M_RIGHT = (1, 2, 3, 14, 15, 16)
H36M_PELVIS, H36M_THORAX = 0, 8
_COCO_TO_H36M_DIRECT = {1: 12, 2: 14, 3: 16, 4: 11, 5: 13, 6: 15, 9: 0, 11: 5, 12: 7, 13: 9, 14: 6, 15: 8, 16: 10}


def coco_to_h36m(points: FloatArray, scores: FloatArray) -> FloatArray:
    """COCO-17 (T, 17, 2) + puntuaciones (T, 17) → Human3.6M (T, 17, 3) con la confianza como 3.er canal.
    Pelvis, tórax y columna son puntos medios; la cabeza se extrapola desde los ojos (no se usa en métricas)."""
    frames = points.shape[0]
    coco = np.concatenate([points, scores[..., None]], axis=-1)
    h36m = np.zeros((frames, 17, 3))
    for target, source in _COCO_TO_H36M_DIRECT.items():
        h36m[:, target] = coco[:, source]
    h36m[:, H36M_PELVIS] = _midpoint(coco[:, 11], coco[:, 12])
    h36m[:, H36M_THORAX] = _midpoint(coco[:, 5], coco[:, 6])
    h36m[:, 7] = _midpoint(h36m[:, H36M_PELVIS], h36m[:, H36M_THORAX])
    eyes = _midpoint(coco[:, 1], coco[:, 2])
    h36m[:, 10, :2] = eyes[:, :2] + 0.5 * (eyes[:, :2] - h36m[:, H36M_THORAX, :2])
    h36m[:, 10, 2] = eyes[:, 2]
    return h36m


def _midpoint(a: FloatArray, b: FloatArray) -> FloatArray:
    mid = (a + b) / 2
    mid[..., 2] = np.minimum(a[..., 2], b[..., 2])  # la confianza del punto derivado es la del peor origen
    return mid


def normalize_pixels(h36m: FloatArray, width: int, height: int) -> FloatArray:
    """Modo --pixel de MotionBERT: centra en la imagen y divide por min(w, h)/2 (la confianza no cambia)."""
    result = h36m.copy()
    result[..., :2] = (h36m[..., :2] - np.array([width, height]) / 2) / (min(width, height) / 2)
    return result


def flip_h36m(data: FloatArray) -> FloatArray:
    """Espejo horizontal: invierte x e intercambia izquierda/derecha."""
    flipped = data.copy()
    flipped[..., 0] *= -1
    flipped[..., list(H36M_LEFT + H36M_RIGHT), :] = flipped[..., list(H36M_RIGHT + H36M_LEFT), :]
    return flipped


@dataclass(frozen=True)
class MotionBertLifter:
    session: InferenceSession
    clip_len: int = 243
    flip: bool = True

    def lift(self, normalized: FloatArray) -> FloatArray:
        """(T, 17, 3) normalizado → (T, 17, 3) en coordenadas de cámara (mismas unidades), por clips.
        El último clip se rellena repitiendo el último fotograma y se recorta después."""
        frames = normalized.shape[0]
        outputs = []
        for start in range(0, frames, self.clip_len):
            chunk = normalized[start:start + self.clip_len]
            length = len(chunk)
            padded = np.concatenate([chunk, np.repeat(chunk[-1:], self.clip_len - length, axis=0)])
            prediction = self._run(padded)
            if self.flip:
                prediction = (prediction + flip_h36m(self._run(flip_h36m(padded)))) / 2
            outputs.append(prediction[:length])
        return np.concatenate(outputs) if outputs else np.zeros((0, 17, 3))

    def _run(self, clip: FloatArray) -> FloatArray:
        (result,) = self.session.run(None, {"input": clip[None].astype(np.float32)})
        prediction: FloatArray = np.asarray(result, dtype=float)[0]
        return prediction


def to_metres(camera: FloatArray, torso_metres: float = 0.5) -> FloatArray:
    """Centra cada fotograma en la pelvis y escala para que la mediana pelvis→tórax mida `torso_metres`.

    MotionBERT no conoce la escala real (una sola cámara no la da). Igual que MediaPipe world, se usa una
    talla de referencia: las métricas angulares y los cocientes no dependen de ella; las de metros sí
    (contact_point_ahead, alturas), en la proporción entre el torso real del jugador y 0,5 m."""
    centered = camera - camera[:, H36M_PELVIS:H36M_PELVIS + 1]
    torso = float(np.median(np.linalg.norm(centered[:, H36M_THORAX], axis=-1)))
    return centered * (torso_metres / torso) if torso > 1e-9 else centered
