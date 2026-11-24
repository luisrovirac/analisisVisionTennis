"""Métricas biomecánicas (forehand, revés, saque y volea).

Son aproximaciones de primera etapa sobre las coordenadas "world" de MediaPipe (centradas en la
cadera, y hacia abajo). El equipo de biomecánica debe validarlas y refinarlas; añadir una métrica
nueva = crear una clase con id/label/unit/compute y registrarla en registry.py (qué golpes la usan).

Ojo: como "world" está centrado en la cadera, no se puede medir el desplazamiento del cuerpo por la
pista (p. ej. la transferencia de peso hacia delante); se usan sustitutos articulares (empuje de piernas).
"""

from __future__ import annotations

import numpy as np

from tennis_worker.contracts import MetricValue
from tennis_worker.pipeline import geometry
from tennis_worker.pipeline.biomechanics.base import MetricContext, measured
from tennis_worker.pipeline.landmarks import LEFT, RIGHT, Landmark
from tennis_worker.pipeline.types import FloatArray

_SHOULDERS = (LEFT.shoulder, RIGHT.shoulder)
_HIPS = (LEFT.hip, RIGHT.hip)
_ANKLES = (LEFT.ankle, RIGHT.ankle)
_NOSE = Landmark.NOSE
_HORIZONTAL = np.array([1.0, 0.0, 1.0])


def _horizontal_distance(a: FloatArray, b: FloatArray) -> FloatArray:
    result: FloatArray = np.linalg.norm((a - b) * _HORIZONTAL, axis=-1)
    return result


def _height_above(point: FloatArray, reference: FloatArray) -> FloatArray:
    """Altura de `point` sobre `reference` en metros (positivo = por encima; el eje y va hacia abajo)."""
    result: FloatArray = reference[..., 1] - point[..., 1]
    return result


class ShoulderRotationRange:
    id = "shoulder_rotation_range"
    label = "Rotación de hombros (preparación → impacto)"
    unit = "deg"

    def compute(self, ctx: MetricContext) -> MetricValue:
        yaw = ctx.shoulder_yaw[ctx.preparation]
        return measured(self, np.ptp(yaw), ctx.support(_SHOULDERS, ctx.preparation))


class HipShoulderSeparationMax:
    id = "hip_shoulder_separation_max"
    label = "Separación cadera-hombros máxima"
    unit = "deg"

    def compute(self, ctx: MetricContext) -> MetricValue:
        frames = ctx.preparation
        separation = np.abs(geometry.wrap_degrees(ctx.shoulder_yaw[frames] - ctx.hip_yaw[frames]))
        return measured(self, separation.max(), ctx.support(_SHOULDERS + _HIPS, frames))


class ElbowAngleAtImpact:
    id = "elbow_angle_at_impact"
    label = "Ángulo del codo en el impacto"
    unit = "deg"

    def compute(self, ctx: MetricContext) -> MetricValue:
        side, i = ctx.sides.dominant, ctx.segment.impact_frame
        angle = geometry.joint_angle(ctx.world(side.shoulder)[i], ctx.world(side.elbow)[i], ctx.world(side.wrist)[i])
        return measured(self, float(angle), ctx.support((side.shoulder, side.elbow, side.wrist), slice(i, i + 1)))


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
        return measured(self, np.minimum(*angles).min(), ctx.support(landmarks, frames))


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
        return measured(self, float(offset @ direction), ctx.support((side.wrist, *_HIPS), slice(b, i + 1)))


class FollowThroughHeight:
    """Altura máxima de la muñeca sobre el hombro dominante tras el impacto (positivo = por encima)."""

    id = "follow_through_height"
    label = "Altura de la terminación sobre el hombro"
    unit = "m"

    def compute(self, ctx: MetricContext) -> MetricValue:
        side, frames = ctx.sides.dominant, ctx.follow_through
        height = ctx.world(side.shoulder)[frames, 1] - ctx.world(side.wrist)[frames, 1]  # y hacia abajo
        return measured(self, height.max(), ctx.support((side.shoulder, side.wrist), frames))


def peaks_in_order(t_hip: int, t_shoulder: int, t_wrist: int, tolerance_frames: int) -> bool:
    """Cadera → hombros → muñeca, admitiendo que un pico llegue hasta `tolerance_frames` antes que el anterior."""
    return t_hip <= t_shoulder + tolerance_frames and t_shoulder <= t_wrist + tolerance_frames


class KineticChainSequence:
    """1 si los picos de velocidad siguen el orden cadera → hombros → muñeca; 0 en caso contrario.

    En golpes de profesional los picos de cadera y hombros están separados por unos 20-50 ms: a 30 fps caen
    en el mismo fotograma o se invierten por uno solo (ruido de la pose). Medido sobre 21 derechas de
    profesionales (Creative Commons, 2026-11-23), el orden estricto marcaba 6 como desordenadas; con la
    tolerancia de ~1 fotograma a 30 fps (`tolerance_s`) quedan las 2 con una inversión clara (>= 2 fotogramas).
    """

    id = "kinetic_chain_sequence"
    label = "Secuencia de la cadena cinética (cadera → hombros → brazo)"
    unit = "bool"
    tolerance_s = 0.034  # resolución temporal: 1 fotograma a 30 fps, 2 a 60 fps

    def compute(self, ctx: MetricContext) -> MetricValue:
        frames, fps = ctx.preparation, ctx.pose.fps
        wrist = ctx.sides.dominant.wrist
        t_hip = int(np.argmax(geometry.angular_speed(ctx.hip_yaw[frames], fps)))
        t_shoulder = int(np.argmax(geometry.angular_speed(ctx.shoulder_yaw[frames], fps)))
        t_wrist = int(np.argmax(geometry.speed(ctx.world(wrist)[frames], fps)))
        tolerance = max(1, round(self.tolerance_s * fps))
        in_order = peaks_in_order(t_hip, t_shoulder, t_wrist, tolerance)
        return measured(self, 1.0 if in_order else 0.0, ctx.support((*_HIPS, *_SHOULDERS, wrist), frames))


# ---------------------------------------------------------------------------------------------------
# Ampliación de la base de conocimiento: métricas de forehand, revés, saque y volea.
# ---------------------------------------------------------------------------------------------------


class StanceWidthRatio:
    """Separación horizontal de los tobillos en el impacto dividida por la anchura de hombros.
    La distancia horizontal 3D no depende de cuánto estén girados los hombros."""

    id = "stance_width_ratio"
    label = "Anchura de la base (tobillos / hombros) en el impacto"
    unit = "ratio"

    def compute(self, ctx: MetricContext) -> MetricValue:
        i = ctx.segment.impact_frame
        feet = _horizontal_distance(ctx.world(LEFT.ankle)[i], ctx.world(RIGHT.ankle)[i])
        shoulders = _horizontal_distance(ctx.world(LEFT.shoulder)[i], ctx.world(RIGHT.shoulder)[i])
        ratio = float(feet) / max(float(shoulders), 1e-6)
        return measured(self, ratio, ctx.support(_ANKLES + _SHOULDERS, slice(i, i + 1)))


class HeadStability:
    """Máximo desplazamiento de la cabeza (nariz) respecto a su posición en el impacto, ±0,1 s, medido EN LA
    IMAGEN y expresado en largos de torso (hombros → cadera en la imagen). Menor = cabeza más quieta.

    No se usan las coordenadas "world": están centradas en la cadera y giran con el cuerpo, así que el giro
    del tronco en el impacto movería la nariz aunque la cabeza estuviera quieta respecto a la pista.
    Supone cámara fija. Limitación: la imagen está normalizada por ancho y alto por separado, así que en un
    video apaisado los movimientos horizontales pesan algo más que los verticales."""

    id = "head_stability"
    label = "Movimiento de la cabeza alrededor del impacto (en largos de torso)"
    unit = "ratio"
    _SECONDS = 0.1

    def compute(self, ctx: MetricContext) -> MetricValue:
        frames, i = ctx.around_impact(self._SECONDS), ctx.segment.impact_frame
        image = ctx.pose.image
        nose = image[:, _NOSE, :2]
        torso = np.linalg.norm(image[i, list(_SHOULDERS), :2].mean(axis=0) - image[i, list(_HIPS), :2].mean(axis=0))
        displacement = np.linalg.norm(nose[frames] - nose[i], axis=-1) / max(float(torso), 1e-6)
        return measured(self, displacement.max(), ctx.support((_NOSE, *_SHOULDERS, *_HIPS), frames))


class RacketDropBelowContact:
    """Cuánto baja la muñeca dominante por debajo de la altura del impacto entre el backswing y el impacto.
    Positivo = el swing va de abajo hacia arriba (necesario para dar efecto liftado)."""

    id = "racket_drop_below_contact"
    label = "Caída de la raqueta por debajo del impacto (swing de abajo arriba)"
    unit = "m"

    def compute(self, ctx: MetricContext) -> MetricValue:
        side, frames, i = ctx.sides.dominant, ctx.backswing_to_impact, ctx.segment.impact_frame
        wrist_y = ctx.world(side.wrist)[:, 1]
        drop = wrist_y[frames].max() - wrist_y[i]  # y hacia abajo: mayor y = más bajo
        return measured(self, drop, ctx.support((side.wrist,), frames))


class LegDrive:
    """Extensión de rodilla entre su máxima flexión en la preparación y el impacto (la pierna que más se
    extiende). Sustituto del empuje desde el suelo: el desplazamiento del cuerpo no es medible en "world"."""

    id = "leg_drive"
    label = "Empuje de piernas (extensión de rodilla hasta el impacto)"
    unit = "deg"

    def compute(self, ctx: MetricContext) -> MetricValue:
        frames, i = ctx.preparation, ctx.segment.impact_frame
        extensions = []
        for s in (LEFT, RIGHT):
            angles = geometry.joint_angle(ctx.world(s.hip), ctx.world(s.knee), ctx.world(s.ankle))
            extensions.append(float(angles[i] - angles[frames].min()))
        landmarks = (LEFT.hip, LEFT.knee, LEFT.ankle, RIGHT.hip, RIGHT.knee, RIGHT.ankle)
        return measured(self, max(extensions), ctx.support(landmarks, frames))


class OffArmExtension:
    """Forehand: máxima distancia horizontal entre la muñeca y el hombro del brazo NO dominante durante la
    preparación. Un brazo libre activo se estira hacia la bola; uno inactivo cuelga junto al cuerpo."""

    id = "off_arm_extension"
    label = "Uso del brazo libre en la preparación"
    unit = "m"

    def compute(self, ctx: MetricContext) -> MetricValue:
        side, frames = ctx.sides.non_dominant, ctx.preparation
        reach = _horizontal_distance(ctx.world(side.wrist)[frames], ctx.world(side.shoulder)[frames])
        return measured(self, reach.max(), ctx.support((side.wrist, side.shoulder), frames))


class UnitTurnLeadTime:
    """Segundos entre el momento en que los hombros alcanzan el 80 % de su giro máximo (respecto a su
    orientación en el impacto) y el impacto. Mayor = preparación más temprana. Si casi no hay giro
    (< 15°) no hay preparación que medir: 0 s. El máximo es la ventana antes del impacto (≈ 1 s)."""

    id = "unit_turn_lead_time"
    label = "Antelación de la preparación (giro de hombros antes del impacto)"
    unit = "s"
    _FRACTION = 0.8
    _MIN_TURN_DEG = 15.0

    def compute(self, ctx: MetricContext) -> MetricValue:
        frames, i = ctx.preparation, ctx.segment.impact_frame
        turn = np.abs(geometry.wrap_degrees(ctx.shoulder_yaw[frames] - ctx.shoulder_yaw[i]))
        support = ctx.support(_SHOULDERS, frames)
        if turn.max() < self._MIN_TURN_DEG:
            return measured(self, 0.0, support)
        reached = ctx.segment.start_frame + int(np.argmax(turn >= self._FRACTION * turn.max()))
        return measured(self, (i - reached) / ctx.pose.fps, support)


class WristSeparationAtImpact:
    """Revés: distancia entre las dos muñecas en el impacto. < 0,2 m ≈ revés a dos manos."""

    id = "wrist_separation_at_impact"
    label = "Separación de las muñecas en el impacto (dos manos < 0,2 m)"
    unit = "m"

    def compute(self, ctx: MetricContext) -> MetricValue:
        i = ctx.segment.impact_frame
        distance = np.linalg.norm(ctx.world(LEFT.wrist)[i] - ctx.world(RIGHT.wrist)[i])
        return measured(self, float(distance), ctx.support((LEFT.wrist, RIGHT.wrist), slice(i, i + 1)))


class ContactHeight:
    """Saque: altura de la muñeca dominante sobre la cabeza (nariz) en el impacto."""

    id = "contact_height"
    label = "Altura del impacto sobre la cabeza"
    unit = "m"

    def compute(self, ctx: MetricContext) -> MetricValue:
        side, i = ctx.sides.dominant, ctx.segment.impact_frame
        height = _height_above(ctx.world(side.wrist)[i], ctx.world(_NOSE)[i])
        return measured(self, float(height), ctx.support((side.wrist, _NOSE), slice(i, i + 1)))


class TrophyElbowAngle:
    """Saque: ángulo del codo del brazo de golpeo en la posición de trofeo (≈ 90° es lo habitual)."""

    id = "trophy_elbow_angle"
    label = "Ángulo del codo en la posición de trofeo"
    unit = "deg"

    def compute(self, ctx: MetricContext) -> MetricValue:
        side, t = ctx.sides.dominant, ctx.trophy_frame
        angle = geometry.joint_angle(ctx.world(side.shoulder)[t], ctx.world(side.elbow)[t], ctx.world(side.wrist)[t])
        return measured(self, float(angle), ctx.support((side.shoulder, side.elbow, side.wrist), slice(t, t + 1)))


class TossArmHeight:
    """Saque: altura de la muñeca del brazo de lanzamiento sobre la cabeza en la posición de trofeo."""

    id = "toss_arm_height"
    label = "Altura del brazo de lanzamiento en la posición de trofeo"
    unit = "m"

    def compute(self, ctx: MetricContext) -> MetricValue:
        side, t = ctx.sides.non_dominant, ctx.trophy_frame
        height = _height_above(ctx.world(side.wrist)[t], ctx.world(_NOSE)[t])
        return measured(self, float(height), ctx.support((side.wrist, _NOSE), slice(t, t + 1)))


class ShoulderTiltAtTrophy:
    """Saque: inclinación de la línea de hombros en la posición de trofeo (grados sobre la horizontal).
    Positivo = el hombro de golpeo está más bajo que el del brazo de lanzamiento ("hombro sobre hombro")."""

    id = "shoulder_tilt_at_trophy"
    label = "Inclinación de hombros en la posición de trofeo"
    unit = "deg"

    def compute(self, ctx: MetricContext) -> MetricValue:
        sides, t = ctx.sides, ctx.trophy_frame
        hitting, tossing = ctx.world(sides.dominant.shoulder)[t], ctx.world(sides.non_dominant.shoulder)[t]
        drop = hitting[1] - tossing[1]  # y hacia abajo: positivo = hombro de golpeo más bajo
        tilt = np.degrees(np.arctan2(drop, float(_horizontal_distance(hitting, tossing))))
        return measured(self, float(tilt), ctx.support(_SHOULDERS, slice(t, t + 1)))


class BackswingWristTravel:
    """Volea: distancia horizontal máxima entre la muñeca en el impacto y su posición en los 0,5 s previos.
    Mide lo largo que es el armado (en la volea debe ser corto)."""

    id = "backswing_wrist_travel"
    label = "Longitud del armado (recorrido de la muñeca antes del impacto)"
    unit = "m"
    _SECONDS = 0.5

    def compute(self, ctx: MetricContext) -> MetricValue:
        side, frames, i = ctx.sides.dominant, ctx.before_impact(self._SECONDS), ctx.segment.impact_frame
        wrist = ctx.world(side.wrist)
        travel = _horizontal_distance(wrist[frames], wrist[i])
        return measured(self, travel.max(), ctx.support((side.wrist,), frames))


class FollowThroughLength:
    """Volea: distancia horizontal máxima que recorre la muñeca en los 0,3 s posteriores al impacto."""

    id = "follow_through_length"
    label = "Longitud de la terminación (recorrido de la muñeca tras el impacto)"
    unit = "m"
    _SECONDS = 0.3

    def compute(self, ctx: MetricContext) -> MetricValue:
        side, frames, i = ctx.sides.dominant, ctx.after_impact(self._SECONDS), ctx.segment.impact_frame
        wrist = ctx.world(side.wrist)
        travel = _horizontal_distance(wrist[frames], wrist[i])
        return measured(self, travel.max(), ctx.support((side.wrist,), frames))
