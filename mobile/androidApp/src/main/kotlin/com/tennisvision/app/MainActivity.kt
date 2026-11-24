package com.tennisvision.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.ui.platform.LocalContext
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.tennisvision.shared.AnalysisProgress
import com.tennisvision.shared.model.Analysis
import com.tennisvision.shared.model.AnalysisSummary
import com.tennisvision.shared.model.Handedness
import com.tennisvision.shared.model.ReferenceComparison
import com.tennisvision.shared.skeleton.Skeleton
import kotlin.math.roundToInt

private val STROKES = listOf(
    null to "Detectar", "forehand" to "Forehand", "backhand" to "Revés", "serve" to "Saque", "volley" to "Volea",
)
private val HANDS = listOf(Handedness.RIGHT to "Diestro", Handedness.LEFT to "Zurdo")

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
        if (viewModel.loggedIn.value) viewModel.loadSession()
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
    var pendingDelete by remember { mutableStateOf<AnalysisSummary?>(null) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        uri?.let { viewModel.analyze(resolver.videoSource(it), stroke) }
    }
    ChipRow(STROKES, selected = stroke, onSelect = { stroke = it })
    ChipRow(HANDS, selected = state.handedness, onSelect = viewModel::setHandedness)
    Row(verticalAlignment = Alignment.CenterVertically) {
        Checkbox(checked = state.expertReviewAllowed, onCheckedChange = viewModel::setExpertReviewAllowed)
        Text("Permitir que expertos vean este video (solo para mejorar las reglas; tu análisis es automático)")
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(enabled = !state.busy, onClick = { picker.launch("video/*") }) { Text("Elegir video y analizar") }
        TextButton(onClick = viewModel::logout) { Text("Cerrar sesión") }
    }
    // El informe con el video puede ser largo: se desplaza en su propia zona y el historial sigue visible.
    state.progress?.let {
        Column(Modifier.heightIn(max = 560.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(8.dp)) { Progress(it, state.media) }
    }
    Text("Mis análisis", style = MaterialTheme.typography.titleMedium)
    LazyColumn(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        items(state.history, key = AnalysisSummary::id) { item ->
            HistoryRow(item, onOpen = { viewModel.open(item.id) }, onDelete = { pendingDelete = item })
        }
    }
    pendingDelete?.let { item ->
        ConfirmDeleteDialog(item, onConfirm = { viewModel.delete(item.id); pendingDelete = null }, onDismiss = { pendingDelete = null })
    }
}

@Composable
private fun <T> ChipRow(options: List<Pair<T, String>>, selected: T, onSelect: (T) -> Unit) {
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEach { (value, label) ->
            FilterChip(selected = selected == value, onClick = { onSelect(value) }, label = { Text(label) })
        }
    }
}

private fun AnalysisSummary.label() = "${createdAt.take(16).replace('T', ' ')} · ${strokeType ?: "detectando…"}"

@Composable
private fun HistoryRow(item: AnalysisSummary, onOpen: () -> Unit, onDelete: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text("${item.label()} · ${item.status.name.lowercase()}",
            Modifier.weight(1f).clickable(onClick = onOpen).padding(vertical = 6.dp))
        // Mientras se procesa, la API rechaza el borrado: el botón se desactiva.
        TextButton(onClick = onDelete, enabled = !item.status.isBusy) {
            Text("Eliminar", color = if (item.status.isBusy) Color.Unspecified else MaterialTheme.colorScheme.error)
        }
    }
}

@Composable
private fun ConfirmDeleteDialog(item: AnalysisSummary, onConfirm: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("¿Eliminar análisis?") },
        text = { Text("Se borrarán el video y el informe del ${item.label()}. No se puede deshacer.") },
        confirmButton = { TextButton(onClick = onConfirm) { Text("Eliminar", color = MaterialTheme.colorScheme.error) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}

@Composable
private fun Progress(progress: AnalysisProgress, media: MediaState?) {
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
        is AnalysisProgress.Completed -> Report(progress.analysis, media)
    }
}

@Composable
private fun Report(analysis: Analysis, media: MediaState?) {
    val report = analysis.report ?: return
    var focus by remember(analysis.id) { mutableStateOf<SkeletonFocus?>(null) }
    val impact = report.segment?.impactFrame ?: 0
    fun show(evidence: Map<String, Double>, strength: Boolean) {
        focus = SkeletonFocus(impact, Skeleton.landmarksFor(report, evidence.keys), strength, System.nanoTime())
    }
    val canShow = media is MediaState.Ready
    report.quality.warnings.forEach { Text("⚠ $it") }
    when (media) {
        MediaState.Loading -> Text("Cargando video y esqueleto…", style = MaterialTheme.typography.labelMedium)
        is MediaState.Unavailable -> Text("No pudimos cargar el video: ${media.message}", style = MaterialTheme.typography.labelMedium)
        is MediaState.Ready -> SkeletonPlayer(media.videoUrl, media.pose, report.segment, focus)
        null -> Unit
    }
    report.diagnosis?.let { Text(it.summary, style = MaterialTheme.typography.bodyLarge) }
    report.referenceComparison?.let { ReferenceCard(it) }
    if (report.strengths.isNotEmpty()) Text("Lo que haces bien", style = MaterialTheme.typography.titleMedium)
    report.strengths.forEach { strength ->
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(strength.title, style = MaterialTheme.typography.titleSmall)
                Text(strength.whyItMatters)
                if (strength.howToMaintain.isNotBlank()) Text("Cómo aprovecharlo: ${strength.howToMaintain}")
                if (canShow) TextButton(onClick = { show(strength.evidence, strength = true) }) { Text("Ver en el video") }
            }
        }
    }
    if (report.findings.isEmpty()) Text("No detectamos errores con las reglas actuales. ¡Buen golpe!")
    report.findings.forEach { finding ->
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("${finding.rank}. ${finding.title}", style = MaterialTheme.typography.titleSmall)
                finding.validated?.let { Text(if (it) "Regla validada" else "Regla provisional", style = MaterialTheme.typography.labelSmall) }
                Text(finding.explanation)
                Text("Corrección: ${finding.correction}")
                if (canShow) TextButton(onClick = { show(finding.evidence, strength = false) }) { Text("Ver en el video") }
            }
        }
    }
    report.drillPlan?.let { plan ->
        Text("Tu plan de ejercicios", style = MaterialTheme.typography.titleMedium)
        plan.items.forEach { item -> Text("Semana ${item.week}: ${item.focus}\n" + item.drills.joinToString("\n") { "• $it" }) }
        Text("Vuelve a grabarte en ${plan.reanalyzeAfterWeeks} semana(s).")
    }
    report.reliability?.let { Text("Fiabilidad del informe: ${RELIABILITY[it.level] ?: it.level}", style = MaterialTheme.typography.labelMedium) }
    Text("Recomendaciones de entrenamiento, no diagnóstico médico.", style = MaterialTheme.typography.labelSmall)
}

private val PHASES = mapOf("preparation" to "Preparación", "acceleration" to "Aceleración", "follow_through" to "Terminación")
private val PHASE_STATUS = mapOf("low_confidence" to "no se vio con suficiente seguridad", "too_short" to "demasiado corta para comparar")

/** Comparación informativa con un golpe de referencia: sin puntaje si no hay referencia o no se vio bien. */
@Composable
private fun ReferenceCard(rc: ReferenceComparison) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Similitud con un golpe de referencia", style = MaterialTheme.typography.titleSmall)
            val overall = rc.overallSimilarity
            if (overall == null) {
                Text(rc.note.ifBlank { "No se pudo comparar este golpe." })
                return@Column
            }
            val ref = rc.referencesUsed.firstOrNull()?.let { " frente a «${it.label}»" }.orEmpty()
            Text("${overall.roundToInt()} / 100$ref", style = MaterialTheme.typography.bodyLarge)
            rc.phases.forEach { p ->
                val label = PHASES[p.phase] ?: p.phase
                val similarity = p.similarity
                if (similarity == null) {
                    Text("$label: ${PHASE_STATUS[p.status] ?: p.status}.", style = MaterialTheme.typography.labelMedium)
                } else {
                    Text("$label: ${similarity.roundToInt()}", style = MaterialTheme.typography.labelMedium)
                    LinearProgressIndicator(progress = { (similarity / 100).toFloat() }, modifier = Modifier.fillMaxWidth())
                }
            }
            if (rc.note.isNotBlank()) Text(rc.note, style = MaterialTheme.typography.labelSmall)
        }
    }
}

private val RELIABILITY = mapOf("high" to "alta", "medium" to "media", "low" to "baja")
