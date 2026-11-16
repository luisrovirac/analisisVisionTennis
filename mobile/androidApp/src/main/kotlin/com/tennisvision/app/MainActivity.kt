package com.tennisvision.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.ui.platform.LocalContext
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.tennisvision.shared.AnalysisProgress
import com.tennisvision.shared.model.Analysis
import com.tennisvision.shared.model.AnalysisSummary
import com.tennisvision.shared.model.CreateAnalysisRequest

private val STROKES = listOf(null to "Detectar", "forehand" to "Forehand")

class MainActivity : ComponentActivity() {
    private val viewModel: AnalysisViewModel by viewModels {
        viewModelFactory {
            initializer {
                val app = application as TennisApp
                AnalysisViewModel(app.auth, app.api, app.flow)
            }
        }
    }
    private lateinit var login: LoginManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        login = LoginManager(this, BuildConfig.OIDC_ISSUER, BuildConfig.OIDC_CLIENT_ID, viewModel::onLogin)
        setContent {
            MaterialTheme {
                val loggedIn by viewModel.loggedIn.collectAsStateWithLifecycle()
                val state by viewModel.state.collectAsStateWithLifecycle()
                Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("Análisis de golpe", style = MaterialTheme.typography.headlineSmall)
                    state.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                    if (loggedIn) {
                        AnalyzeScreen(state, viewModel)
                    } else {
                        Text("Inicia sesión para analizar tus golpes y ver tu historial.")
                        Button(onClick = login::login) { Text("Iniciar sesión") }
                    }
                }
            }
        }
        if (viewModel.loggedIn.value) viewModel.refreshHistory()
    }

    override fun onDestroy() {
        login.dispose()
        super.onDestroy()
    }
}

@Composable
private fun AnalyzeScreen(state: UiState, viewModel: AnalysisViewModel) {
    val resolver = LocalContext.current.contentResolver
    var stroke by remember { mutableStateOf<String?>("forehand") }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        uri?.let { viewModel.analyze(resolver.videoSource(it), CreateAnalysisRequest(strokeType = stroke)) }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        STROKES.forEach { (value, label) ->
            FilterChip(selected = stroke == value, onClick = { stroke = value }, label = { Text(label) })
        }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(enabled = !state.busy, onClick = { picker.launch("video/*") }) { Text("Elegir video y analizar") }
        TextButton(onClick = viewModel::logout) { Text("Cerrar sesión") }
    }
    state.progress?.let { Progress(it) }
    Text("Mis análisis", style = MaterialTheme.typography.titleMedium)
    LazyColumn(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        items(state.history, key = AnalysisSummary::id) { item ->
            Text("${item.createdAt.take(16).replace('T', ' ')} · ${item.strokeType ?: "detectando…"} · ${item.status.name.lowercase()}",
                Modifier.fillMaxWidth().clickable { viewModel.open(item.id) }.padding(vertical = 6.dp))
        }
    }
}

@Composable
private fun Progress(progress: AnalysisProgress) {
    when (progress) {
        AnalysisProgress.Creating -> Text("Preparando la subida…")
        is AnalysisProgress.Uploading -> {
            Text("Subiendo video…")
            LinearProgressIndicator(progress = { progress.fraction }, modifier = Modifier.fillMaxWidth())
        }
        is AnalysisProgress.Waiting -> {
            Text("Analizando tu golpe (${progress.status.name.lowercase()})…")
            LinearProgressIndicator(Modifier.fillMaxWidth())
        }
        is AnalysisProgress.Failed -> Text(progress.message, color = MaterialTheme.colorScheme.error)
        is AnalysisProgress.Completed -> Report(progress.analysis)
    }
}

@Composable
private fun Report(analysis: Analysis) {
    val report = analysis.report ?: return
    report.quality.warnings.forEach { Text("⚠ $it") }
    if (report.findings.isEmpty()) Text("No detectamos errores con las reglas actuales. ¡Buen golpe!")
    report.findings.forEach { finding ->
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("${finding.rank}. ${finding.title}", style = MaterialTheme.typography.titleSmall)
                Text(finding.explanation)
                Text("Corrección: ${finding.correction}")
            }
        }
    }
}
