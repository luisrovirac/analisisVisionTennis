package com.tennisvision.shared

import com.tennisvision.shared.api.ApiException
import com.tennisvision.shared.api.TennisApi
import com.tennisvision.shared.api.VideoSource
import com.tennisvision.shared.api.apiClient
import com.tennisvision.shared.api.plainClient
import com.tennisvision.shared.auth.AuthRepository
import com.tennisvision.shared.auth.SessionExpiredException
import com.tennisvision.shared.auth.TokenRefresher
import com.tennisvision.shared.auth.TokenStore
import com.tennisvision.shared.auth.Tokens
import com.tennisvision.shared.model.AnalysisStatus
import com.tennisvision.shared.model.CreateAnalysisRequest
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.MockRequestHandleScope
import io.ktor.client.engine.mock.respond
import io.ktor.client.request.HttpRequestData
import io.ktor.client.request.HttpResponseData
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpMethod
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import io.ktor.utils.io.writeFully
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertTrue

private const val BASE = "http://api.test"
private const val UPLOAD = "http://storage.test/videos/a1?X-Amz-Signature=x"

class MemoryStore(var tokens: Tokens?) : TokenStore {
    override fun load() = tokens
    override fun save(tokens: Tokens) { this.tokens = tokens }
    override fun clear() { tokens = null }
}

class CountingRefresher(private val now: () -> Long) : TokenRefresher {
    var calls = 0
    override suspend fun refresh(refreshToken: String): Tokens {
        calls++
        return Tokens("renewed-$calls", null, null, now() + 300_000)
    }
}

private fun analysisJson(status: String, extra: String = "") =
    """{"id":"a1","status":"$status","stroke_type":null,"error":null,"report":null,
       "created_at":"2026-11-15T10:00:00Z","updated_at":"2026-11-15T10:00:00Z"$extra}"""

private val completedReport = """, "report": {"stroke_type":"forehand","metrics":[],"findings":[
    {"rule_id":"FH-1","rank":1,"category":"fundamental","title":"t","explanation":"e","correction":"c","drills":[],"evidence":{}}],
    "quality":{"pose_detection_rate":0.9,"warnings":[]},
    "classification":{"stroke":"forehand","confidence":0.93,"classifier_id":"pose_seq","model_version":"v1"},
    "campo_futuro": 1}"""

private fun MockRequestHandleScope.json(body: String, status: HttpStatusCode = HttpStatusCode.OK): HttpResponseData =
    respond(body, status, headersOf(HttpHeaders.ContentType, "application/json"))

class Harness(handler: suspend MockRequestHandleScope.(HttpRequestData) -> HttpResponseData) {
    var now = 1_000_000L
    val store = MemoryStore(Tokens("access-1", "refresh-1", "id-1", now + 600_000))
    val refresher = CountingRefresher { now }
    val auth = AuthRepository(store, refresher, clock = { now })
    val requests = mutableListOf<HttpRequestData>()
    private val engine = MockEngine { request -> requests += request; handler(request) }
    val api = TennisApi(apiClient(engine, auth), plainClient(engine), BASE)
}

private val tinyVideo = VideoSource(4, "video/mp4") { it.writeFully(byteArrayOf(1, 2, 3, 4)) }

class AnalysisFlowTest {
    @Test
    fun fullFlowUploadsDirectlyAndPollsUntilCompleted() = runTest {
        var polls = 0
        val h = Harness { request ->
            when {
                request.method == HttpMethod.Post && request.url.encodedPath == "/v1/analyses" ->
                    json("""{"analysis":${analysisJson("awaiting_upload")},"upload_url":"$UPLOAD","expires_at":"x"}""",
                        HttpStatusCode.Created)
                request.method == HttpMethod.Put -> respond("", HttpStatusCode.OK)
                request.url.encodedPath.endsWith("/submit") -> json(analysisJson("queued"), HttpStatusCode.Accepted)
                else -> json(if (++polls < 3) analysisJson("processing") else analysisJson("completed", completedReport))
            }
        }
        val events = AnalysisFlow(h.api, pollInitialMillis = 1).run(CreateAnalysisRequest(), tinyVideo).toList()

        val done = assertIs<AnalysisProgress.Completed>(events.last())
        assertEquals("pose_seq", done.analysis.report?.classification?.classifierId)
        assertTrue(events.any { it is AnalysisProgress.Waiting && it.status == AnalysisStatus.PROCESSING })
        val upload = h.requests.single { it.method == HttpMethod.Put }
        assertEquals(null, upload.headers[HttpHeaders.Authorization], "la URL prefirmada no lleva bearer")
        assertTrue(h.requests.filter { it.url.host == "api.test" }.all { it.headers[HttpHeaders.Authorization] == "Bearer access-1" })
    }

    @Test
    fun failedAnalysisSurfacesUserMessage() = runTest {
        val h = Harness {
            json(analysisJson("failed").replace("\"error\":null",
                "\"error\":{\"code\":\"NO_PERSON_DETECTED\",\"message\":\"No detectamos a ningún jugador.\"}"))
        }
        val last = AnalysisFlow(h.api, pollInitialMillis = 1).poll("a1").toList().last()
        assertEquals(AnalysisProgress.Failed("NO_PERSON_DETECTED", "No detectamos a ningún jugador."), last)
    }

    @Test
    fun apiErrorsCarryCodeAndMessage() = runTest {
        val h = Harness { json("""{"error":{"code":"UPLOAD_MISSING","message":"Sube el video."}}""", HttpStatusCode.Conflict) }
        val error = assertFailsWith<ApiException> { h.api.submit("a1") }
        assertEquals("UPLOAD_MISSING", error.code)
        assertEquals("Sube el video.", error.userMessage())
    }
}

class AuthRepositoryTest {
    @Test
    fun refreshesProactivelyBeforeExpiry() = runTest {
        val h = Harness { json("""{"items":[],"next_cursor":null}""") }
        h.now += 560_000 // quedan 40 s: dentro del margen de 60 s
        h.api.list()
        assertEquals(1, h.refresher.calls)
        assertEquals("Bearer renewed-1", h.requests.last().headers[HttpHeaders.Authorization])
        assertEquals("refresh-1", h.store.tokens?.refreshToken, "conserva el refresh token si el IdP no envía otro")
    }

    @Test
    fun concurrentRequestsShareASingleRefresh() = runTest {
        val h = Harness { json("""{"items":[],"next_cursor":null}""") }
        h.now += 590_000
        (1..5).map { async { h.api.list() } }.awaitAll()
        assertEquals(1, h.refresher.calls)
    }

    @Test
    fun unauthorizedResponseEndsSession() = runTest {
        val h = Harness { json("""{"error":{"code":"UNAUTHENTICATED","message":"x"}}""", HttpStatusCode.Unauthorized) }
        assertFailsWith<SessionExpiredException> { h.api.list() }
        assertFalse(h.auth.loggedIn.value)
        assertEquals(null, h.store.tokens)
    }

    @Test
    fun noSessionFailsWithoutCallingTheApi() = runTest {
        val h = Harness { json("{}") }
        h.auth.logout()
        assertFailsWith<SessionExpiredException> { h.api.list() }
        assertTrue(h.requests.isEmpty())
    }
}
