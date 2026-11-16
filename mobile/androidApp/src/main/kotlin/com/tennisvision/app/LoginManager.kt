package com.tennisvision.app

import android.content.Intent
import android.net.Uri
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import com.tennisvision.shared.auth.Tokens
import net.openid.appauth.AppAuthConfiguration
import net.openid.appauth.AuthorizationRequest
import net.openid.appauth.AuthorizationResponse
import net.openid.appauth.AuthorizationService
import net.openid.appauth.AuthorizationServiceConfiguration
import net.openid.appauth.ResponseTypeValues
import net.openid.appauth.connectivity.ConnectionBuilder
import net.openid.appauth.connectivity.DefaultConnectionBuilder
import java.net.HttpURLConnection
import java.net.URL

/**
 * Login con Authorization Code + PKCE en el navegador del sistema (AppAuth añade PKCE solo).
 * Es la única parte de la autenticación propia de Android: los tokens resultantes se entregan al
 * AuthRepository compartido, que gestiona el resto del ciclo de vida.
 */
class LoginManager(
    activity: ComponentActivity,
    private val issuer: String,
    private val clientId: String,
    private val onResult: (Result<Tokens>) -> Unit,
) {
    private val connections: ConnectionBuilder = if (BuildConfig.DEBUG) DevConnectionBuilder else DefaultConnectionBuilder.INSTANCE
    private val service = AuthorizationService(activity, AppAuthConfiguration.Builder().setConnectionBuilder(connections).build())
    private val launcher = activity.registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        handle(result.data)
    }

    fun login() {
        AuthorizationServiceConfiguration.fetchFromIssuer(Uri.parse(issuer), { config, error ->
            if (config == null) return@fetchFromIssuer onResult(Result.failure(error ?: IllegalStateException("discovery")))
            val request = AuthorizationRequest.Builder(config, clientId, ResponseTypeValues.CODE, Uri.parse(REDIRECT_URI))
                .setScopes("openid", "email", "profile")
                .build()
            launcher.launch(service.getAuthorizationRequestIntent(request))
        }, connections)
    }

    private fun handle(data: Intent?) {
        val response = data?.let { AuthorizationResponse.fromIntent(it) }
            ?: return onResult(Result.failure(IllegalStateException("Inicio de sesión cancelado.")))
        service.performTokenRequest(response.createTokenExchangeRequest()) { token, error ->
            val access = token?.accessToken
            if (token == null || access == null) return@performTokenRequest onResult(Result.failure(error ?: IllegalStateException("token")))
            onResult(Result.success(Tokens(access, token.refreshToken, token.idToken, token.accessTokenExpirationTime ?: 0L)))
        }
    }

    fun dispose() = service.dispose()

    private companion object {
        const val REDIRECT_URI = "com.tennisvision.app:/oauth2redirect"
    }
}

/** Solo debug: AppAuth exige HTTPS; el Keycloak local habla HTTP (vía adb reverse en localhost). */
private object DevConnectionBuilder : ConnectionBuilder {
    override fun openConnection(uri: Uri): HttpURLConnection =
        (URL(uri.toString()).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout = 10_000
            instanceFollowRedirects = false
        }
}
