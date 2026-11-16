package com.tennisvision.shared.api

import com.tennisvision.shared.auth.AuthRepository
import io.ktor.client.HttpClient
import io.ktor.client.engine.HttpClientEngine
import io.ktor.client.plugins.api.createClientPlugin
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.serialization.kotlinx.json.json
import kotlinx.serialization.json.Json

val TennisJson = Json {
    ignoreUnknownKeys = true // Postel: la API puede añadir campos sin romper versiones antiguas de la app
    explicitNulls = false
}

/** Motor HTTP de cada plataforma: OkHttp en Android, Darwin (NSURLSession) en iOS. */
expect fun platformEngine(): HttpClientEngine

/**
 * Cliente de la API: añade el bearer token a cada petición (renovándolo antes si va a caducar).
 * Se usa un plugin propio en lugar del Auth de Ktor porque este cachea el token y no renueva de forma
 * proactiva; aquí la fuente de verdad es siempre [AuthRepository].
 */
fun apiClient(engine: HttpClientEngine, auth: AuthRepository): HttpClient = HttpClient(engine) {
    expectSuccess = false
    install(ContentNegotiation) { json(TennisJson) }
    install(bearerFrom(auth))
}

/** Cliente sin autenticación: para la URL prefirmada (ya lleva su firma) y para el IdP. */
fun plainClient(engine: HttpClientEngine): HttpClient = HttpClient(engine) {
    expectSuccess = false
    install(ContentNegotiation) { json(TennisJson) }
}

private fun bearerFrom(auth: AuthRepository) = createClientPlugin("BearerFromAuthRepository") {
    onRequest { request, _ ->
        request.headers[HttpHeaders.Authorization] = "Bearer ${auth.validAccessToken()}"
    }
    onResponse { response ->
        if (response.status == HttpStatusCode.Unauthorized) auth.onUnauthorized()
    }
}
