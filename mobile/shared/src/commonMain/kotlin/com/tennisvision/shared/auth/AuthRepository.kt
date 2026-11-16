package com.tennisvision.shared.auth

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable

@Serializable
data class Tokens(
    val accessToken: String,
    val refreshToken: String?,
    val idToken: String?,
    /** Instante de expiración del access token, en milisegundos desde epoch. */
    val expiresAtMillis: Long,
)

/** Almacenamiento seguro por plataforma: Android Keystore, Keychain en iOS. */
interface TokenStore {
    fun load(): Tokens?
    fun save(tokens: Tokens)
    fun clear()
}

/** Intercambia un refresh token por tokens nuevos (endpoint de tokens del IdP). */
fun interface TokenRefresher {
    suspend fun refresh(refreshToken: String): Tokens
}

class SessionExpiredException : Exception("La sesión ha caducado. Inicia sesión de nuevo.")

/**
 * Ciclo de vida del token compartido por Android e iOS. El login (navegador + PKCE) es de cada
 * plataforma; al terminar, la plataforma entrega los tokens con [onLogin].
 *
 * - Refresco proactivo: si al token le quedan menos de [refreshMargin] ms, se renueva antes de usarlo.
 * - Un único refresco a la vez: peticiones concurrentes esperan al mismo resultado (Mutex).
 */
class AuthRepository(
    private val store: TokenStore,
    private val refresher: TokenRefresher,
    private val clock: () -> Long,
    private val refreshMargin: Long = 60_000,
) {
    private val mutex = Mutex()
    private val _loggedIn = MutableStateFlow(store.load() != null)
    val loggedIn: StateFlow<Boolean> = _loggedIn.asStateFlow()

    fun onLogin(tokens: Tokens) {
        store.save(tokens)
        _loggedIn.value = true
    }

    fun idToken(): String? = store.load()?.idToken

    fun logout() {
        store.clear()
        _loggedIn.value = false
    }

    /** Access token vigente; lanza [SessionExpiredException] si no hay sesión o no se puede renovar. */
    suspend fun validAccessToken(): String = mutex.withLock {
        val tokens = store.load() ?: throw expire()
        if (clock() < tokens.expiresAtMillis - refreshMargin) return tokens.accessToken
        val refreshToken = tokens.refreshToken ?: throw expire()
        val renewed = runCatching { refresher.refresh(refreshToken) }.getOrElse { throw expire() }
        store.save(renewed.copy(refreshToken = renewed.refreshToken ?: refreshToken, idToken = renewed.idToken ?: tokens.idToken))
        renewed.accessToken
    }

    /** La API respondió 401 aunque el token parecía vigente (revocado, reloj desfasado...). */
    fun onUnauthorized(): Nothing = throw expire()

    private fun expire(): SessionExpiredException {
        logout()
        return SessionExpiredException()
    }
}
