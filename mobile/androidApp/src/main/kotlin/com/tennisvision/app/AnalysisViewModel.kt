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
import com.tennisvision.shared.userMessage
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class UiState(
    val progress: AnalysisProgress? = null,
    val history: List<AnalysisSummary> = emptyList(),
    val error: String? = null,
    val busy: Boolean = false,
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

    fun onLogin(result: Result<Tokens>) {
        result.onSuccess { auth.onLogin(it); refreshHistory() }
            .onFailure { e -> _state.update { it.copy(error = e.userMessage()) } }
    }

    fun logout() {
        auth.logout()
        _state.value = UiState()
    }

    fun analyze(video: VideoSource, request: CreateAnalysisRequest) = follow(flow.run(request, video))

    fun open(id: String) = follow(flow.poll(id))

    fun refreshHistory() {
        viewModelScope.launch {
            runCatching { api.list() }
                .onSuccess { page -> _state.update { it.copy(history = page.items) } }
                .onFailure { e -> _state.update { it.copy(error = e.userMessage()) } }
        }
    }

    private fun follow(progress: Flow<AnalysisProgress>) {
        running?.cancel()
        _state.update { it.copy(busy = true, error = null, progress = null) }
        running = viewModelScope.launch {
            progress
                .catch { e -> _state.update { it.copy(error = e.userMessage()) } }
                .collect { p -> _state.update { it.copy(progress = p) } }
            _state.update { it.copy(busy = false) }
            refreshHistory()
        }
    }
}
