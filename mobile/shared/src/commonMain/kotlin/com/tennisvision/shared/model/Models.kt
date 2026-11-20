package com.tennisvision.shared.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Espejo de la API HTTP v1 (Docs/CONTRATOS.md) y de contracts/report.schema.json.
// El Json se configura con ignoreUnknownKeys: los campos nuevos de la API no rompen la app.

@Serializable
data class CreateAnalysisRequest(
    /** null = que el worker detecte el golpe automáticamente. */
    @SerialName("stroke_type") val strokeType: String? = null,
    val handedness: String = Handedness.RIGHT,
    @SerialName("camera_view") val cameraView: String = "side",
    /** Checkbox "Permitir que expertos vean este video", marcado por defecto (solo control de calidad de reglas). */
    @SerialName("expert_review_allowed") val expertReviewAllowed: Boolean = true,
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
    val handedness: String = Handedness.RIGHT,
    @SerialName("camera_view") val cameraView: String = "side",
    @SerialName("expert_review_allowed") val expertReviewAllowed: Boolean = true,
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

    /** Mientras el worker lo procesa, la API rechaza el borrado (409 ANALYSIS_BUSY). */
    val isBusy: Boolean get() = this == QUEUED || this == PROCESSING
}

/** Preferencias guardadas en la cuenta del usuario (GET/PUT /v1/me/preferences). */
@Serializable
data class Preferences(val handedness: String = Handedness.RIGHT)

object Handedness {
    const val RIGHT = "right"
    const val LEFT = "left"
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
    // Revisión 1.1 del contrato (aditiva): todos opcionales; un reporte 1.0 se sigue leyendo igual.
    val strengths: List<Strength> = emptyList(),
    val diagnosis: Diagnosis? = null,
    @SerialName("drill_plan") val drillPlan: DrillPlan? = null,
    val reliability: Reliability? = null,
    @SerialName("knowledge_status") val knowledgeStatus: KnowledgeStatus? = null,
)

@Serializable
data class Strength(
    @SerialName("rule_id") val ruleId: String,
    val rank: Int,
    val title: String,
    @SerialName("why_it_matters") val whyItMatters: String,
    @SerialName("how_to_maintain") val howToMaintain: String = "",
    val confidence: Double = 1.0,
)

@Serializable
data class Diagnosis(
    val summary: String,
    @SerialName("root_cause_rule_id") val rootCauseRuleId: String? = null,
    @SerialName("contributing_factors") val contributingFactors: List<String> = emptyList(),
    val limitations: List<String> = emptyList(),
    val unmeasurable: List<String> = emptyList(),
)

@Serializable
data class DrillPlan(
    val weeks: Int,
    @SerialName("reanalyze_after_weeks") val reanalyzeAfterWeeks: Int,
    val items: List<DrillPlanItem> = emptyList(),
)

@Serializable
data class DrillPlanItem(
    val week: Int,
    @SerialName("focus_rule_id") val focusRuleId: String,
    val focus: String,
    val drills: List<String> = emptyList(),
)

@Serializable
data class Reliability(val score: Double, val level: String, val reasons: List<String> = emptyList())

@Serializable
data class KnowledgeStatus(
    val status: String,
    @SerialName("validated_rules") val validatedRules: Int,
    @SerialName("total_rules") val totalRules: Int,
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
    /** 1.1: null en reportes antiguos; true = regla validada por el equipo de expertos. */
    val validated: Boolean? = null,
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
