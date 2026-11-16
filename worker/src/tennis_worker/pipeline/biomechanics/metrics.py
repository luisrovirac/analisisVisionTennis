"""Métricas iniciales del forehand.

Son aproximaciones de primera etapa sobre las coordenadas "world" de MediaPipe (centradas en la
cadera, y hacia abajo). El equipo de biomecánica debe validarlas y refinarlas; añadir una métrica
nueva = crear una clase con id/label/unit/compute y registrarla en registry.py.
"""

from __future__ import annotations

import numpy as np

from tennis_worker.contracts import MetricValue
from tennis_worker.pipeline import geometry
from tennis_worker.pipeline.biomechanics.base import MetricContext, measured
from tennis_worker.pipeline.landmarks import LEFT, RIGHT

_SHOULDERS = (LEFT.shoulder, RIGHT.shoulder)
_HIPS = (LEFT.hip, RIGHT.hip)


class ShoulderRotationRange:
    id = "shoulder_rotation_range"
    label = "Rotación de hombros (preparación → impacto)"
    unit = "deg"

    def compute(self, ctx: MetricContext) -> MetricValue:
        yaw = ctx.shoulder_yaw[ctx.preparation]
        return measured(self, np.ptp(yaw), ctx.confidence(_SHOULDERS, ctx.preparation))


class HipShoulderSeparationMax:
    id = "hip_shoulder_separation_max"
    label = "Separación cadera-hombros máxima"
    unit = "deg"

    def compute(self, ctx: MetricContext) -> MetricValue:
        frames = ctx.preparation
        separation = np.abs(geometry.wrap_degrees(ctx.shoulder_yaw[frames] - ctx.hip_yaw[frames]))
        return measured(self, separation.max(), ctx.confidence(_SHOULDERS + _HIPS, frames))


class ElbowAngleAtImpact:
    id = "elbow_angle_at_impact"
    label = "Ángulo del codo en el impacto"
    unit = "deg"

    def compute(self, ctx: MetricContext) -> MetricValue:
        side, i = ctx.sides.dominant, ctx.segment.impact_frame
        angle = geometry.joint_angle(ctx.world(side.shoulder)[i], ctx.world(side.elbow)[i], ctx.world(side.wrist)[i])
        return measured(self, float(angle), ctx.confidence((side.shoulder, side.elbow, side.wrist), slice(i, i + 1)))


class KneeAngleMin:
    id = "knee_angle_min"
    label = "Flexión de rodillas (ángulo mínimo; 180 = pierna recta)"
    unit = "deg"

    def compute(self, ctx: MetricContext) -> MetricValue:
        frames = ctx.preparation
        angles = [
            geometry.joint_angle(ctx.world(s.hip)[frames], ctx.world(s.knee)[frames], ctx.world(s.ankle)[frames])
            for s in (LEFT, RIGHT)
        ]
        landmarks = (LEFT.hip, LEFT.knee, LEFT.ankle, RIGHT.hip, RIGHT.knee, RIGHT.ankle)
        return measured(self, np.minimum(*angles).min(), ctx.confidence(landmarks, frames))


class ContactPointAhead:
    """Posición horizontal de la muñeca en el impacto respecto al centro de la cadera, proyectada sobre la
    dirección global del swing (muñeca en backswing → muñeca en impacto). Positivo = impacto adelantado.

    No se usa la velocidad instantánea: en un arco alrededor del cuerpo es perpendicular a la posición
    y la proyección sería siempre ≈ 0."""

    id = "contact_point_ahead"
    label = "Punto de impacto adelantado"
    unit = "m"
    _HORIZONTAL = np.array([1.0, 0.0, 1.0])

    def compute(self, ctx: MetricContext) -> MetricValue:
        side, i, b = ctx.sides.dominant, ctx.segment.impact_frame, ctx.segment.backswing_frame
        wrist = ctx.world(side.wrist)
        direction = (wrist[i] - wrist[b]) * self._HORIZONTAL
        direction /= np.linalg.norm(direction) + 1e-9
        hip_center = (ctx.world(LEFT.hip)[i] + ctx.world(RIGHT.hip)[i]) / 2
        offset = (wrist[i] - hip_center) * self._HORIZONTAL
        return measured(self, float(offset @ direction), ctx.confidence((side.wrist, *_HIPS), slice(b, i + 1)))


class FollowThroughHeight:
    """Altura máxima de la muñeca sobre el hombro dominante tras el impacto (positivo = por encima)."""

    id = "follow_through_height"
    label = "Altura de la terminación sobre el hombro"
    unit = "m"

    def compute(self, ctx: MetricContext) -> MetricValue:
        side, frames = ctx.sides.dominant, ctx.follow_through
        height = ctx.world(side.shoulder)[frames, 1] - ctx.world(side.wrist)[frames, 1]  # y hacia abajo
        return measured(self, height.max(), ctx.confidence((side.shoulder, side.wrist), frames))


class KineticChainSequence:
    """1 si los picos de velocidad siguen el orden cadera → hombros → muñeca; 0 en caso contrario."""

    id = "kinetic_chain_sequence"
    label = "Secuencia de la cadena cinética (cadera → hombros → brazo)"
    unit = "bool"

    def compute(self, ctx: MetricContext) -> MetricValue:
        frames, fps = ctx.preparation, ctx.pose.fps
        wrist = ctx.sides.dominant.wrist
        t_hip = np.argmax(geometry.angular_speed(ctx.hip_yaw[frames], fps))
        t_shoulder = np.argmax(geometry.angular_speed(ctx.shoulder_yaw[frames], fps))
        t_wrist = np.argmax(geometry.speed(ctx.world(wrist)[frames], fps))
        in_order = t_hip <= t_shoulder <= t_wrist
        return measured(self, 1.0 if in_order else 0.0, ctx.confidence((*_HIPS, *_SHOULDERS, wrist), frames))
