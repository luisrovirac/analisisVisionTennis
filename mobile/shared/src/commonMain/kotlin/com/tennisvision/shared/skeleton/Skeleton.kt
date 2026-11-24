package com.tennisvision.shared.skeleton

import com.tennisvision.shared.model.PoseFrame
import com.tennisvision.shared.model.PoseSequence
import com.tennisvision.shared.model.Report
import kotlin.math.floor

/**
 * Lógica del visor de esqueleto, compartida entre plataformas (la web usa la misma en skeleton-viewer.js).
 * Índices MediaPipe Pose de 33 puntos: los produce MediaPipe y también RTMPose + MotionBERT (mapeado).
 */
object Skeleton {
    const val NOSE = 0
    const val LEFT_SHOULDER = 11
    const val RIGHT_SHOULDER = 12

    /** Huesos sin cara ni manos (RTMPose no los estima y no aportan al análisis). */
    val BONES: List<Pair<Int, Int>> = listOf(
        11 to 12, 11 to 23, 12 to 24, 23 to 24, // tronco
        11 to 13, 13 to 15, 12 to 14, 14 to 16, // brazos
        23 to 25, 25 to 27, 24 to 26, 26 to 28, // piernas
        27 to 29, 29 to 31, 27 to 31, 28 to 30, 30 to 32, 28 to 32, // pies
    )
    val LEFT: Set<Int> = setOf(11, 13, 15, 23, 25, 27, 29, 31)
    val RIGHT: Set<Int> = setOf(12, 14, 16, 24, 26, 28, 30, 32)
    val JOINTS: List<Int> = listOf(NOSE) + LEFT + RIGHT

    /** Misma "puerta de evidencia" que el worker: por debajo, la articulación se dibuja tenue. */
    const val RELIABLE = 0.5
    private const val NEVER_SEEN = 0.05

    /** Fotograma mostrado en el instante [positionMs] (con margen para no caer en el anterior por redondeo). */
    fun frameAt(positionMs: Long, fps: Double, frameCount: Int): Int {
        if (frameCount <= 0) return 0
        val frame = floor(positionMs / 1000.0 * fps + 0.01).toInt()
        return frame.coerceIn(0, frameCount - 1)
    }

    /** Instante (ms) a mitad del fotograma: buscar ahí evita mostrar el fotograma anterior. */
    fun positionOf(frame: Int, fps: Double): Long = ((frame + 0.5) / fps * 1000.0).toLong()

    /** Landmarks que el estimador llegó a ver alguna vez (los demás no se dibujan, p. ej. pies con RTMPose). */
    fun everSeen(pose: PoseSequence): BooleanArray {
        val seen = BooleanArray(33)
        for (f in pose.frames) {
            if (!f.detected) continue
            f.visibility.forEachIndexed { i, v -> if (i < 33 && v > NEVER_SEEN) seen[i] = true }
        }
        return seen
    }

    /** Un punto se puede dibujar si se vio alguna vez y no es el (0,0) con que se serializan los NaN. */
    fun usable(frame: PoseFrame, seen: BooleanArray, i: Int): Boolean {
        val p = frame.image.getOrNull(i) ?: return false
        return seen.getOrElse(i) { false } && !(p[0] == 0.0 && p[1] == 0.0)
    }

    /** Visibilidad efectiva: un fotograma interpolado (sin detección) cuenta como 0. */
    fun visibility(frame: PoseFrame, i: Int): Double = if (frame.detected) frame.visibility.getOrElse(i) { 0.0 } else 0.0

    /** Unión de los landmarks de las métricas citadas como evidencia (report 1.2: metrics[].landmarks). */
    fun landmarksFor(report: Report, metricIds: Collection<String>): Set<Int> {
        val byId = report.metrics.associateBy { it.id }
        return metricIds.flatMap { byId[it]?.landmarks.orEmpty() }.toSet()
    }

    /**
     * ¿La orientación del video coincide con la analizada? El worker (OpenCV) y el reproductor aplican por
     * separado la rotación de los metadatos; si no coinciden, el esqueleto quedaría girado.
     */
    fun aspectMatches(pose: PoseSequence, videoWidth: Int, videoHeight: Int): Boolean {
        val w = pose.width ?: return true
        val h = pose.height ?: return true
        if (videoWidth <= 0 || videoHeight <= 0 || h == 0) return true
        val poseAspect = w.toDouble() / h
        val videoAspect = videoWidth.toDouble() / videoHeight
        return kotlin.math.abs(videoAspect - poseAspect) / poseAspect <= 0.03
    }
}
