package com.tennisvision.shared.api

import com.tennisvision.shared.model.Analysis
import com.tennisvision.shared.model.AnalysisPage
import com.tennisvision.shared.model.ApiError
import com.tennisvision.shared.model.CreateAnalysisRequest
import com.tennisvision.shared.model.MediaLinks
import com.tennisvision.shared.model.PoseSequence
import com.tennisvision.shared.model.Preferences
import com.tennisvision.shared.model.UploadTicket
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.plugins.onUpload
import io.ktor.client.request.delete
import io.ktor.client.request.get
import io.ktor.client.request.parameter
import io.ktor.client.request.post
import io.ktor.client.request.put
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.http.contentType
import io.ktor.http.content.OutgoingContent
import io.ktor.http.isSuccess
import io.ktor.utils.io.ByteWriteChannel
import kotlinx.serialization.Serializable

/** Error de la API con su mensaje pensado para el usuario final. */
class ApiException(val code: String, override val message: String, val status: Int) : Exception(message)

/**
 * Video a subir, sin cargarlo en memoria (pueden ser 200 MB): cada plataforma abre su flujo
 * (ContentResolver en Android, NSInputStream en iOS) y lo copia al canal.
 */
class VideoSource(
    val sizeBytes: Long,
    val contentType: String,
    val writeTo: suspend (ByteWriteChannel) -> Unit,
)

class TennisApi(
    private val api: HttpClient,
    private val storage: HttpClient,
    baseUrl: String,
) {
    private val base = baseUrl.trimEnd('/')

    suspend fun create(request: CreateAnalysisRequest): UploadTicket =
        api.post("$base/v1/analyses") {
            contentType(ContentType.Application.Json)
            setBody(request)
        }.parse()

    /** PUT directo al almacenamiento (MinIO/R2): el video nunca pasa por la API. */
    suspend fun upload(url: String, video: VideoSource, onProgress: (Float) -> Unit = {}) {
        val response = storage.put(url) {
            setBody(StreamingBody(video))
            onUpload { sent, total -> if (total != null && total > 0) onProgress(sent.toFloat() / total) }
        }
        if (!response.status.isSuccess()) {
            throw ApiException("UPLOAD_FAILED", "La subida del video falló (${response.status.value}).", response.status.value)
        }
    }

    suspend fun submit(id: String): Analysis = api.post("$base/v1/analyses/$id/submit").parse()

    suspend fun get(id: String): Analysis = api.get("$base/v1/analyses/$id").parse()

    /** URLs firmadas del video y de la pose para el visor de esqueleto (solo análisis completados). */
    suspend fun media(id: String): MediaLinks = api.get("$base/v1/analyses/$id/media").parse()

    /** Descarga la pose por fotograma desde su URL firmada (sin bearer: la firma ya autoriza). */
    suspend fun pose(url: String): PoseSequence = storage.get(url).parse()

    suspend fun list(cursor: String? = null, limit: Int = 20): AnalysisPage =
        api.get("$base/v1/analyses") {
            parameter("limit", limit)
            cursor?.let { parameter("cursor", it) }
        }.parse()

    /** Borra el análisis con su video. Un 404 cuenta como éxito: el análisis ya no existe. */
    suspend fun delete(id: String) {
        val response = api.delete("$base/v1/analyses/$id")
        if (response.status != HttpStatusCode.NotFound) response.ensureSuccess()
    }

    suspend fun preferences(): Preferences = api.get("$base/v1/me/preferences").parse()

    suspend fun savePreferences(preferences: Preferences): Preferences =
        api.put("$base/v1/me/preferences") {
            contentType(ContentType.Application.Json)
            setBody(preferences)
        }.parse()
}

private class StreamingBody(private val video: VideoSource) : OutgoingContent.WriteChannelContent() {
    override val contentType: ContentType = ContentType.parse(video.contentType)
    override val contentLength: Long = video.sizeBytes
    override suspend fun writeTo(channel: ByteWriteChannel) = video.writeTo(channel)
}

@Serializable
private data class ErrorEnvelope(val error: ApiError)

private suspend inline fun <reified T> HttpResponse.parse(): T {
    ensureSuccess()
    return body()
}

private suspend fun HttpResponse.ensureSuccess() {
    if (status.isSuccess()) return
    val error = runCatching { body<ErrorEnvelope>().error }.getOrNull()
    throw ApiException(error?.code ?: "HTTP_${status.value}", error?.message ?: "Error ${status.value}", status.value)
}
