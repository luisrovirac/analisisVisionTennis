package com.tennisvision.shared

import com.tennisvision.shared.api.TennisJson
import com.tennisvision.shared.model.PoseSequence
import com.tennisvision.shared.model.Report
import com.tennisvision.shared.skeleton.Skeleton
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class SkeletonTest {
    private fun frame(detected: Boolean, visibility: Double, x: Double = 0.5): String {
        val image = (0 until 33).joinToString(",") { "[$x,0.5,0.0]" }
        val vis = (0 until 33).joinToString(",") { "$visibility" }
        return """{"index":0,"detected":$detected,"image":[$image],"world":[$image],"visibility":[$vis]}"""
    }

    @Test
    fun frameAtMapsPlaybackTimeToFrameIndex() {
        assertEquals(0, Skeleton.frameAt(0, 30.0, 90))
        assertEquals(29, Skeleton.frameAt(999, 30.0, 90))
        assertEquals(30, Skeleton.frameAt(1000, 30.0, 90))
        assertEquals(89, Skeleton.frameAt(60_000, 30.0, 90)) // nunca se sale de la secuencia
        assertEquals(41, Skeleton.frameAt(Skeleton.positionOf(41, 30.0), 30.0, 90)) // buscar y leer es estable
    }

    @Test
    fun poseArtifactDecodesIgnoringWorldCoordinates() {
        val json = """{"schema_version":"1.0","fps":30.0,"landmark_count":33,"width":1280,"height":720,
            "frames":[${frame(true, 0.9)},${frame(false, 0.0)}]}"""
        val pose = TennisJson.decodeFromString<PoseSequence>(json)
        assertEquals(2, pose.frames.size)
        assertEquals(1280, pose.width)
        assertEquals(33, pose.frames[0].image.size)
    }

    @Test
    fun neverSeenAndInterpolatedPointsAreHandled() {
        val pose = TennisJson.decodeFromString<PoseSequence>(
            """{"fps":30.0,"frames":[${frame(true, 0.9)},${frame(false, 0.0)}]}""")
        val seen = Skeleton.everSeen(pose)
        assertTrue(seen.all { it })
        assertEquals(0.0, Skeleton.visibility(pose.frames[1], 11)) // interpolado = sin confianza
        val zero = TennisJson.decodeFromString<PoseSequence>("""{"fps":30.0,"frames":[${frame(false, 0.0, x = 0.0)}]}""")
        assertFalse(Skeleton.everSeen(zero).any { it })
    }

    @Test
    fun aspectMismatchIsDetected() {
        val pose = PoseSequence(fps = 30.0, width = 1280, height = 720)
        assertTrue(Skeleton.aspectMatches(pose, 1920, 1080))
        assertFalse(Skeleton.aspectMatches(pose, 1080, 1920)) // video girado
        assertTrue(Skeleton.aspectMatches(PoseSequence(fps = 30.0), 1080, 1920)) // pose antigua: sin datos
    }

    @Test
    fun report12FieldsParseAndHighlightUnionOfEvidence() {
        val report = TennisJson.decodeFromString<Report>(
            """{"stroke_type":"forehand","quality":{"pose_detection_rate":1.0,"warnings":[]},
              "segment":{"fps":30,"start_frame":11,"backswing_frame":27,"impact_frame":41,"end_frame":71},
              "metrics":[{"id":"a","label":"A","value":1,"unit":"deg","confidence":0.9,"landmarks":[12,14,16]},
                         {"id":"b","label":"B","value":1,"unit":"m","confidence":0.9,"landmarks":[16,24]},
                         {"id":"old","label":"Old","value":1,"unit":"m","confidence":0.9}],
              "findings":[{"rule_id":"R","rank":1,"category":"fundamental","title":"t","explanation":"e",
                           "correction":"c","evidence":{"a":100.0,"b":0.1}}],
              "reference_comparison":{"status":"ok","overall_similarity":98.4,
                "references_used":[{"ref_id":"x","label":"Modelo"}],
                "phases":[{"phase":"preparation","status":"ok","similarity":99.1,"confidence":0.98},
                          {"phase":"follow_through","status":"low_confidence","confidence":0.2}]}}""")
        assertEquals(41, report.segment?.impactFrame)
        assertEquals(setOf(12, 14, 16, 24), Skeleton.landmarksFor(report, report.findings[0].evidence.keys))
        assertEquals(emptySet(), Skeleton.landmarksFor(report, listOf("old")))
        assertEquals(98.4, report.referenceComparison?.overallSimilarity)
        assertNull(report.referenceComparison?.phases?.get(1)?.similarity)
    }
}
