"""Errores de análisis esperables: no se reintentan y se muestran al usuario con un mensaje accionable."""

from __future__ import annotations

from typing import ClassVar


class AnalysisError(Exception):
    code: ClassVar[str] = "ANALYSIS_ERROR"
    user_message: ClassVar[str] = "No pudimos analizar el video."

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.user_message)
        self.detail = detail


class VideoUnreadableError(AnalysisError):
    code = "VIDEO_UNREADABLE"
    user_message = "No pudimos leer el video. Usa un archivo MP4 o MOV grabado con el móvil."


class VideoTooLongError(AnalysisError):
    code = "VIDEO_TOO_LONG"
    user_message = "El video es demasiado largo. Recórtalo a un solo golpe (3 a 10 segundos)."


class NoPersonDetectedError(AnalysisError):
    code = "NO_PERSON_DETECTED"
    user_message = "No detectamos a ningún jugador. Asegúrate de que tu cuerpo entero sea visible y haya buena luz."


class LowPoseQualityError(AnalysisError):
    code = "LOW_POSE_QUALITY"
    user_message = (
        "Solo vemos al jugador en parte del video. Graba de perfil, a 3–6 m, con el cuerpo entero en cuadro."
    )


class NoStrokeFoundError(AnalysisError):
    code = "NO_STROKE_FOUND"
    user_message = "No encontramos un golpe en el video. Asegúrate de grabar el swing completo."


class UnsupportedStrokeError(AnalysisError):
    code = "UNSUPPORTED_STROKE"
    user_message = "Ese tipo de golpe aún no está disponible. Por ahora analizamos el forehand."


class StrokeNotRecognizedError(UnsupportedStrokeError):
    """Mismo código que UnsupportedStrokeError (los clientes ya lo manejan), otro mensaje."""

    user_message = (
        "No pudimos reconocer el tipo de golpe con seguridad. Indica el golpe al crear el análisis "
        "o graba de perfil un solo golpe completo."
    )
