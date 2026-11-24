package com.tennisvision.app

import androidx.annotation.OptIn
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.VideoSize
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import com.tennisvision.shared.model.PoseSequence
import com.tennisvision.shared.model.Segment
import com.tennisvision.shared.skeleton.Skeleton
import kotlin.math.max
import kotlin.math.min

/** Petición externa de "ver en el video": salta al fotograma y resalta los landmarks. [nonce] repite la misma. */
data class SkeletonFocus(val frame: Int, val landmarks: Set<Int>, val strength: Boolean, val nonce: Long)

private val LEFT_COLOR = Color(0xFF4FC3F7)
private val RIGHT_COLOR = Color(0xFFFFB74D)
private val CENTER_COLOR = Color(0xFFF5F5F5)
private val INTERPOLATED_COLOR = Color(0xFF9E9E9E)
private val ERROR_COLOR = Color(0xFFFF5252)
private val STRENGTH_COLOR = Color(0xFF69F0AE)
private val SPEEDS = listOf(0.25f, 0.5f, 1f)

/**
 * Video del análisis con el esqueleto superpuesto (misma lógica que la web: Skeleton en :shared).
 * Si el decodificador no admite el video o su orientación no coincide con la analizada, se anima solo
 * el esqueleto sobre fondo neutro.
 */
@OptIn(UnstableApi::class)
@Composable
fun SkeletonPlayer(videoUrl: String, pose: PoseSequence, segment: Segment?, focus: SkeletonFocus?) {
    val context = LocalContext.current
    val frames = pose.frames
    val seen = remember(pose) { Skeleton.everSeen(pose) }
    var frame by remember { mutableIntStateOf(0) }
    var playing by remember { mutableStateOf(false) }
    var speed by remember { mutableFloatStateOf(0.5f) }
    var showSkeleton by remember { mutableStateOf(true) }
    var skeletonOnly by remember { mutableStateOf<String?>(null) } // motivo, o null si hay video
    var videoAspect by remember { mutableFloatStateOf(0f) }
    var highlight by remember { mutableStateOf<SkeletonFocus?>(null) }
    var syntheticMs by remember { mutableFloatStateOf(0f) }
    val durationMs = (frames.size / pose.fps * 1000).toFloat()

    val player = remember(videoUrl) {
        ExoPlayer.Builder(context).build().apply {
            setMediaItem(MediaItem.fromUri(videoUrl))
            playbackParameters = playbackParameters.withSpeed(0.5f)
            prepare()
        }
    }
    DisposableEffect(player) {
        val listener = object : Player.Listener {
            override fun onPlayerError(error: PlaybackException) {
                skeletonOnly = "El teléfono no puede reproducir este video; se muestra solo el esqueleto."
            }

            override fun onVideoSizeChanged(videoSize: VideoSize) {
                if (videoSize.width <= 0 || videoSize.height <= 0) return
                val w = videoSize.width * videoSize.pixelWidthHeightRatio
                videoAspect = w / videoSize.height
                if (!Skeleton.aspectMatches(pose, w.toInt(), videoSize.height)) {
                    skeletonOnly = "La orientación del video no coincide con la analizada; se muestra solo el esqueleto."
                }
            }

            override fun onIsPlayingChanged(isPlaying: Boolean) {
                if (skeletonOnly == null) playing = isPlaying
            }
        }
        player.addListener(listener)
        onDispose {
            player.removeListener(listener)
            player.release()
        }
    }

    fun seekTo(target: Int) {
        val f = target.coerceIn(0, max(0, frames.size - 1))
        playing = false
        player.pause()
        if (skeletonOnly == null) player.seekTo(Skeleton.positionOf(f, pose.fps)) else syntheticMs = Skeleton.positionOf(f, pose.fps).toFloat()
        frame = f
    }

    // Reloj: el del reproductor, o uno propio en modo solo esqueleto.
    LaunchedEffect(player, skeletonOnly) {
        if (skeletonOnly != null) {
            player.pause()
            syntheticMs = player.currentPosition.toFloat()
        }
        var last = 0L
        while (true) {
            withFrameNanos { now ->
                if (skeletonOnly == null) {
                    frame = Skeleton.frameAt(player.currentPosition, pose.fps, frames.size)
                } else {
                    if (playing && last != 0L) {
                        syntheticMs += (now - last) / 1_000_000f * speed
                        if (syntheticMs >= durationMs) { syntheticMs = durationMs; playing = false }
                    }
                    frame = Skeleton.frameAt(syntheticMs.toLong(), pose.fps, frames.size)
                }
                last = now
            }
        }
    }
    LaunchedEffect(focus) {
        focus ?: return@LaunchedEffect
        highlight = focus.takeIf { it.landmarks.isNotEmpty() }
        seekTo(focus.frame)
    }

    val poseAspect = if (pose.width != null && pose.height != null && pose.height!! > 0) pose.width!!.toFloat() / pose.height!! else 16f / 9f
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Box(Modifier.fillMaxWidth().aspectRatio(poseAspect).background(if (skeletonOnly != null) Color(0xFF1B211E) else Color.Black)) {
            if (skeletonOnly == null) {
                AndroidView(
                    factory = { ctx ->
                        PlayerView(ctx).apply {
                            useController = false
                            resizeMode = AspectRatioFrameLayout.RESIZE_MODE_FIT
                            this.player = player
                        }
                    },
                    modifier = Modifier.fillMaxSize(),
                )
            }
            Canvas(Modifier.fillMaxSize()) {
                if (showSkeleton) {
                    val aspect = if (skeletonOnly == null && videoAspect > 0f) videoAspect else poseAspect
                    drawSkeleton(pose, frame, seen, aspect, highlight)
                }
            }
        }
        if (frames.size > 1) {
            Slider(
                value = frame.toFloat(),
                onValueChange = { seekTo(it.toInt()) },
                valueRange = 0f..(frames.size - 1).toFloat(),
            )
        }
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically) {
            OutlinedButton(onClick = {
                if (playing) {
                    playing = false
                    player.pause()
                } else {
                    playing = true
                    if (skeletonOnly == null) player.play() else if (syntheticMs >= durationMs) syntheticMs = 0f
                }
            }) { Text(if (playing) "Pausa" else "Reproducir") }
            OutlinedButton(onClick = { seekTo(frame - 1) }) { Text("‹ 1") }
            OutlinedButton(onClick = { seekTo(frame + 1) }) { Text("1 ›") }
            SPEEDS.forEach { s ->
                FilterChip(selected = speed == s, onClick = {
                    speed = s
                    player.playbackParameters = player.playbackParameters.withSpeed(s)
                }, label = { Text("${s}×") })
            }
        }
        segment?.let { seg ->
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("Inicio" to seg.startFrame, "Armado" to seg.backswingFrame, "Impacto" to seg.impactFrame, "Final" to seg.endFrame)
                    .forEach { (label, f) -> OutlinedButton(onClick = { seekTo(f) }) { Text(label) } }
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Switch(checked = showSkeleton, onCheckedChange = { showSkeleton = it })
            Text("Esqueleto · fotograma ${frame + 1}/${frames.size}", style = MaterialTheme.typography.labelMedium)
        }
        Text(
            "Trazo tenue = articulación vista con poca seguridad; gris = fotograma estimado (sin detección).",
            style = MaterialTheme.typography.labelSmall,
        )
        skeletonOnly?.let { Text(it, style = MaterialTheme.typography.labelSmall) }
    }
}

private fun DrawScope.drawSkeleton(pose: PoseSequence, index: Int, seen: BooleanArray, aspect: Float, focus: SkeletonFocus?) {
    val f = pose.frames.getOrNull(index) ?: return
    // Rectángulo real del video dentro de la caja (RESIZE_MODE_FIT deja bandas).
    val cw = min(size.width, size.height * aspect)
    val ch = cw / aspect
    val ox = (size.width - cw) / 2
    val oy = (size.height - ch) / 2
    fun pt(i: Int) = Offset(ox + f.image[i][0].toFloat() * cw, oy + f.image[i][1].toFloat() * ch)
    fun usable(i: Int) = Skeleton.usable(f, seen, i)
    fun vis(i: Int) = Skeleton.visibility(f, i)
    val width = max(4f, ch / 160f)
    val dashed = PathEffect.dashPathEffect(floatArrayOf(14f, 10f))
    val mark = if (focus?.strength == true) STRENGTH_COLOR else ERROR_COLOR

    fun bone(a: Int, b: Int, color: Color, w: Float, faint: Boolean) =
        drawLine(color, pt(a), pt(b), strokeWidth = w, cap = StrokeCap.Round,
            alpha = if (faint) 0.45f else 1f, pathEffect = if (faint) dashed else null)

    for ((a, b) in Skeleton.BONES) {
        if (!usable(a) || !usable(b)) continue
        val color = when {
            !f.detected -> INTERPOLATED_COLOR
            a in Skeleton.LEFT && b in Skeleton.LEFT -> LEFT_COLOR
            a in Skeleton.RIGHT && b in Skeleton.RIGHT -> RIGHT_COLOR
            else -> CENTER_COLOR
        }
        bone(a, b, color, width, min(vis(a), vis(b)) < Skeleton.RELIABLE)
    }
    if (usable(Skeleton.NOSE) && usable(Skeleton.LEFT_SHOULDER) && usable(Skeleton.RIGHT_SHOULDER)) {
        val neck = (pt(Skeleton.LEFT_SHOULDER) + pt(Skeleton.RIGHT_SHOULDER)) / 2f
        val faint = minOf(vis(Skeleton.NOSE), vis(Skeleton.LEFT_SHOULDER), vis(Skeleton.RIGHT_SHOULDER)) < Skeleton.RELIABLE
        drawLine(if (f.detected) CENTER_COLOR else INTERPOLATED_COLOR, pt(Skeleton.NOSE), neck, strokeWidth = width,
            cap = StrokeCap.Round, alpha = if (faint) 0.45f else 1f, pathEffect = if (faint) dashed else null)
    }
    focus?.let { hl ->
        for ((a, b) in Skeleton.BONES) {
            if (a in hl.landmarks && b in hl.landmarks && usable(a) && usable(b)) bone(a, b, mark, width * 2.2f, false)
        }
    }
    for (i in Skeleton.JOINTS) {
        if (!usable(i)) continue
        val marked = focus != null && i in focus.landmarks
        val color = when {
            marked -> mark
            f.detected -> Color.White
            else -> INTERPOLATED_COLOR
        }
        drawCircle(color, radius = if (marked) width * 2.4f else width * 1.3f, center = pt(i),
            alpha = if (vis(i) < Skeleton.RELIABLE && !marked) 0.45f else 1f)
    }
}
