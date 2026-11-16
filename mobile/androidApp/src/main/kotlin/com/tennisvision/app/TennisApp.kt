package com.tennisvision.app

import android.app.Application
import android.content.ContentResolver
import android.net.Uri
import android.provider.OpenableColumns
import com.tennisvision.shared.AnalysisFlow
import com.tennisvision.shared.api.TennisApi
import com.tennisvision.shared.api.VideoSource
import com.tennisvision.shared.api.apiClient
import com.tennisvision.shared.api.plainClient
import com.tennisvision.shared.api.platformEngine
import com.tennisvision.shared.auth.AuthRepository
import com.tennisvision.shared.auth.OidcClient
import io.ktor.utils.io.writeFully
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Raíz de composición: el único sitio donde se instancian las implementaciones concretas. */
class TennisApp : Application() {
    lateinit var auth: AuthRepository
        private set
    lateinit var api: TennisApi
        private set
    lateinit var flow: AnalysisFlow
        private set

    override fun onCreate() {
        super.onCreate()
        val engine = platformEngine()
        val plain = plainClient(engine)
        val clock = System::currentTimeMillis
        auth = AuthRepository(KeystoreTokenStore(this), OidcClient(plain, BuildConfig.OIDC_ISSUER, BuildConfig.OIDC_CLIENT_ID, clock), clock)
        api = TennisApi(apiClient(engine, auth), plain, BuildConfig.API_BASE_URL)
        flow = AnalysisFlow(api)
    }
}

private const val BUFFER_BYTES = 64 * 1024

/** El video se copia en streaming desde el ContentResolver: nunca se carga entero en memoria. */
fun ContentResolver.videoSource(uri: Uri): VideoSource {
    val size = query(uri, arrayOf(OpenableColumns.SIZE), null, null, null)?.use { cursor ->
        if (cursor.moveToFirst()) cursor.getLong(0) else null
    } ?: error("No se pudo leer el tamaño del video.")
    return VideoSource(size, getType(uri) ?: "video/mp4") { channel ->
        withContext(Dispatchers.IO) {
            openInputStream(uri)?.use { input ->
                val buffer = ByteArray(BUFFER_BYTES)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    channel.writeFully(buffer, 0, read)
                }
            } ?: error("No se pudo abrir el video.")
        }
    }
}
