package com.tennisvision.app

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tennisvision.shared.AnalysisFlow
import com.tennisvision.shared.AnalysisProgress
import com.tennisvision.shared.api.TennisApi
import com.tennisvision.shared.api.VideoSource
import com.tennisvision.shared.auth.AuthRepository
import com.tennisvision.shared.auth.Tokens
import com.tennisvision.shared.model.AnalysisSummary
import com.tennisvision.shared.model.CreateAnalysisRequest
import com.tennisvision.shared.model.Handedness
import com.tennisvision.shared.model.PoseSequence
import com.tennisvision.shared.model.Preferences
import com.tennisvision.shared.userMessage
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Video + esqueleto del análisis mostrado (GET /media y la pose firmada), cargado aparte del informe. */
sealed interface MediaState {
    data object Loading : MediaState
    data class Ready(val videoUrl: String, val pose: PoseSequence) : MediaState
    data class Unavailable(val message: String) : MediaState
}

data class UiState(
    val progress: AnalysisProgress? = null,
    val history: List<AnalysisSummary> = emptyList(),
    val error: String? = null,
    val busy: Boolean = false,
    /** Guardada en la cuenta del usuario: se lee al iniciar sesión y se guarda en cada cambio. */
    val handedness: String = Handedness.RIGHT,
    /** Checkbox "Permitir que expertos vean este video": marcado por defecto en cada subida. */
    val expertReviewAllowed: Boolean = true,
    val media: MediaState? = null,
)

/** Delgado: orquesta los casos de uso compartidos y expone estado; la lógica de red está en :shared. */
class AnalysisViewModel(
    private val auth: AuthRepository,
    private val api: TennisApi,
    private val flow: AnalysisFlow,
) : ViewModel() {
    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()
    val loggedIn: StateFlow<Boolean> = auth.loggedIn
    private var running: Job? = null
    private var mediaJob: Job? = null
    private var shownAnalysisId: String? = null

    fun onLogin(result: Result<Tokens>) {
        result.onSuccess { auth.onLogin(it); loadSession() }
            .onFailure { e -> _state.update { it.copy(error = e.userMessage()) } }
    }

    /** Lo que se carga al comenzar cada sesión: preferencias e historial. */
    fun loadSession() {
        loadPreferences()
        refreshHistory()
    }

    fun logout() {
        auth.logout()
        _state.value = UiState()
    }

    fun analyze(video: VideoSource, strokeType: String?) = follow(
        flow.run(
            CreateAnalysisRequest(
                strokeType = strokeType,
                handedness = _state.value.handedness,
                expertReviewAllowed = _state.value.expertReviewAllowed,
            ),
            video,
        ),
    )

    fun open(id: String) {
        shownAnalysisId = id
        follow(flow.poll(id))
    }

    /** Se aplica al momento en la UI; si no se puede guardar, se vuelve al valor anterior. */
    fun setHandedness(handedness: String) {
        val previous = _state.value.handedness
        if (handedness == previous) return
        _state.update { it.copy(handedness = handedness, error = null) }
        viewModelScope.launch {
            runCatching { api.savePreferences(Preferences(handedness)) }
                .onFailure { e -> _state.update { it.copy(handedness = previous, error = e.userMessage()) } }
        }
    }

    fun setExpertReviewAllowed(allowed: Boolean) = _state.update { it.copy(expertReviewAllowed = allowed) }

    fun delete(id: String) {
        viewModelScope.launch {
            runCatching { api.delete(id) }
                .onSuccess {
                    if (shownAnalysisId == id) {
                        running?.cancel()
                        mediaJob?.cancel()
                        shownAnalysisId = null
                        _state.update { it.copy(progress = null, busy = false, media = null) }
                    }
                    refreshHistory()
                }
                .onFailure { e -> _state.update { it.copy(error = e.userMessage()) } }
        }
    }

    fun refreshHistory() {
        viewModelScope.launch {
            runCatching { api.list() }
                .onSuccess { page -> _state.update { it.copy(history = page.items) } }
                .onFailure { e -> _state.update { it.copy(error = e.userMessage()) } }
        }
    }

    private fun loadMedia(id: String) {
        mediaJob?.cancel()
        _state.update { it.copy(media = MediaState.Loading) }
        mediaJob = viewModelScope.launch {
            runCatching {
                val links = api.media(id)
                MediaState.Ready(links.videoUrl, api.pose(links.poseUrl))
            }.onSuccess { media -> if (shownAnalysisId == id) _state.update { it.copy(media = media) } }
                .onFailure { e ->
                    if (shownAnalysisId == id) _state.update { it.copy(media = MediaState.Unavailable(e.userMessage())) }
                }
        }
    }

    private fun loadPreferences() {
        viewModelScope.launch {
            runCatching { api.preferences() }
                .onSuccess { prefs -> _state.update { it.copy(handedness = prefs.handedness) } }
                .onFailure { e -> _state.update { it.copy(error = e.userMessage()) } }
        }
    }

    private fun follow(progress: Flow<AnalysisProgress>) {
        running?.cancel()
        mediaJob?.cancel()
        _state.update { it.copy(busy = true, error = null, progress = null, media = null) }
        running = viewModelScope.launch {
            progress
                .catch { e -> _state.update { it.copy(error = e.userMessage()) } }
                .collect { p ->
                    if (p is AnalysisProgress.Completed) {
                        shownAnalysisId = p.analysis.id
                        loadMedia(p.analysis.id)
                    }
                    _state.update { it.copy(progress = p) }
                }
            _state.update { it.copy(busy = false) }
            refreshHistory()
        }
    }
}
