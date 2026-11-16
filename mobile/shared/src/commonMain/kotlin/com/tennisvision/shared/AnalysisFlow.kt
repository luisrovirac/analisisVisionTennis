package com.tennisvision.shared

import com.tennisvision.shared.api.ApiException
import com.tennisvision.shared.api.TennisApi
import com.tennisvision.shared.api.VideoSource
import com.tennisvision.shared.model.Analysis
import com.tennisvision.shared.model.AnalysisStatus
import com.tennisvision.shared.model.CreateAnalysisRequest
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.flow.flow

sealed interface AnalysisProgress {
    data object Creating : AnalysisProgress
    data class Uploading(val fraction: Float) : AnalysisProgress
    data class Waiting(val status: AnalysisStatus) : AnalysisProgress
    data class Completed(val analysis: Analysis) : AnalysisProgress
    data class Failed(val code: String, val message: String) : AnalysisProgress
}

/**
 * El flujo completo, idéntico al del cliente web (api/web/static/app.js):
 * crear → subir directo al almacenamiento → submit → polling hasta completed/failed.
 * Las excepciones de red y de sesión se propagan al llamador (la UI decide si reintentar o pedir login).
 */
class AnalysisFlow(
    private val api: TennisApi,
    private val pollInitialMillis: Long = 2_000,
    private val pollMaxMillis: Long = 10_000,
) {
    fun run(request: CreateAnalysisRequest, video: VideoSource): Flow<AnalysisProgress> = channelFlow {
        send(AnalysisProgress.Creating)
        val ticket = api.create(request)
        api.upload(ticket.uploadUrl, video) { fraction -> trySend(AnalysisProgress.Uploading(fraction)) }
        api.submit(ticket.analysis.id)
        poll(ticket.analysis.id).collect { send(it) }
    }

    /** También sirve para retomar un análisis del historial que aún no ha terminado. */
    fun poll(id: String): Flow<AnalysisProgress> = flow {
        var wait = pollInitialMillis
        while (true) {
            val analysis = api.get(id)
            when (analysis.status) {
                AnalysisStatus.COMPLETED -> return@flow emit(AnalysisProgress.Completed(analysis))
                AnalysisStatus.FAILED -> return@flow emit(failed(analysis))
                else -> emit(AnalysisProgress.Waiting(analysis.status))
            }
            delay(wait)
            wait = (wait * 3 / 2).coerceAtMost(pollMaxMillis) // backoff: no martillear la API en análisis largos
        }
    }

    private fun failed(analysis: Analysis) = AnalysisProgress.Failed(
        analysis.error?.code ?: "FAILED",
        analysis.error?.message ?: "El análisis falló.",
    )
}

/** Mensaje para el usuario a partir de cualquier error del flujo. */
fun Throwable.userMessage(): String = when (this) {
    is ApiException -> message
    else -> message ?: "Error de conexión. Comprueba tu red e inténtalo de nuevo."
}
