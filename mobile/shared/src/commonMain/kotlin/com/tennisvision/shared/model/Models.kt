package com.tennisvision.shared.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Espejo de la API HTTP v1 (Docs/CONTRATOS.md) y de contracts/report.schema.json.
// El Json se configura con ignoreUnknownKeys: los campos nuevos de la API no rompen la app.

@Serializable
data class CreateAnalysisRequest(
    /** null = que el worker detecte el golpe automáticamente. */
    @SerialName("stroke_type") val strokeType: String? = null,
    val handedness: String = "right",
    @SerialName("camera_view") val cameraView: String = "side",
)

@Serializable
data class UploadTicket(
    val analysis: Analysis,
    @SerialName("upload_url") val uploadUrl: String,
    @SerialName("expires_at") val expiresAt: String,
)

@Serializable
data class ApiError(val code: String, val message: String)

@Serializable
data class Analysis(
    val id: String,
    val status: AnalysisStatus,
    @SerialName("stroke_type") val strokeType: String? = null,
    val handedness: String = "right",
    @SerialName("camera_view") val cameraView: String = "side",
    val error: ApiError? = null,
    val report: Report? = null,
    @SerialName("created_at") val createdAt: String,
    @SerialName("updated_at") val updatedAt: String,
)

@Serializable
enum class AnalysisStatus {
    @SerialName("awaiting_upload") AWAITING_UPLOAD,
    @SerialName("queued") QUEUED,
    @SerialName("processing") PROCESSING,
    @SerialName("completed") COMPLETED,
    @SerialName("failed") FAILED;

    val isFinal: Boolean get() = this == COMPLETED || this == FAILED
}

@Serializable
data class AnalysisSummary(
    val id: String,
    val status: AnalysisStatus,
    @SerialName("stroke_type") val strokeType: String? = null,
    val error: ApiError? = null,
    @SerialName("created_at") val createdAt: String,
)

@Serializable
data class AnalysisPage(
    val items: List<AnalysisSummary>,
    @SerialName("next_cursor") val nextCursor: String? = null,
)

@Serializable
data class Report(
    @SerialName("stroke_type") val strokeType: String,
    val metrics: List<MetricValue> = emptyList(),
    val findings: List<Finding> = emptyList(),
    val quality: Quality,
    val classification: Classification? = null,
)

@Serializable
data class MetricValue(val id: String, val label: String, val value: Double, val unit: String, val confidence: Double)

@Serializable
data class Finding(
    @SerialName("rule_id") val ruleId: String,
    val rank: Int,
    val category: String,
    val title: String,
    val explanation: String,
    val correction: String,
    val drills: List<String> = emptyList(),
)

@Serializable
data class Quality(
    @SerialName("pose_detection_rate") val poseDetectionRate: Double,
    val warnings: List<String> = emptyList(),
)

@Serializable
data class Classification(
    val stroke: String,
    val confidence: Double,
    @SerialName("classifier_id") val classifierId: String,
    @SerialName("model_version") val modelVersion: String? = null,
)

@Serializable
data class AuthConfig(
    val enabled: Boolean,
    val issuer: String? = null,
    @SerialName("client_id") val clientId: String? = null,
)
