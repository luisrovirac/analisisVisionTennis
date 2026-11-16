package com.tennisvision.shared.auth

import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.request.forms.submitForm
import io.ktor.client.request.get
import io.ktor.http.Parameters
import io.ktor.http.isSuccess
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class OidcEndpoints(
    @SerialName("authorization_endpoint") val authorizationEndpoint: String,
    @SerialName("token_endpoint") val tokenEndpoint: String,
    @SerialName("end_session_endpoint") val endSessionEndpoint: String? = null,
)

@Serializable
private data class TokenResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String? = null,
    @SerialName("id_token") val idToken: String? = null,
    @SerialName("expires_in") val expiresIn: Long,
)

/** Discovery y refresco de tokens: HTTP puro, así que se comparte entre plataformas. */
class OidcClient(
    private val http: HttpClient,
    private val issuer: String,
    private val clientId: String,
    private val clock: () -> Long,
) : TokenRefresher {
    private var cached: OidcEndpoints? = null

    suspend fun endpoints(): OidcEndpoints =
        cached ?: http.get("${issuer.trimEnd('/')}/.well-known/openid-configuration").body<OidcEndpoints>().also { cached = it }

    override suspend fun refresh(refreshToken: String): Tokens {
        val response = http.submitForm(
            url = endpoints().tokenEndpoint,
            formParameters = Parameters.build {
                append("grant_type", "refresh_token")
                append("client_id", clientId)
                append("refresh_token", refreshToken)
            },
        )
        check(response.status.isSuccess()) { "el IdP rechazó el refresco (${response.status})" }
        val body = response.body<TokenResponse>()
        return Tokens(body.accessToken, body.refreshToken, body.idToken, clock() + body.expiresIn * 1000)
    }
}
