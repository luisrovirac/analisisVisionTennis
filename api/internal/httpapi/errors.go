package httpapi

import (
	"errors"
	"log/slog"
	"net/http"

	"github.com/gin-gonic/gin"

	"tennisvision/api/internal/domain"
)

type errorMapping struct {
	target  error
	status  int
	code    string
	message string // vacío = usar el texto del error (útil para validaciones)
}

// Tabla de traducción error de dominio → respuesta HTTP accionable (en lugar de if/else dispersos).
var errorMappings = []errorMapping{
	{domain.ErrInvalidInput, http.StatusBadRequest, "INVALID_INPUT", ""},
	{domain.ErrUnauthenticated, http.StatusUnauthorized, "UNAUTHENTICATED",
		"Tu sesión no es válida o ha caducado. Vuelve a iniciar sesión."},
	{domain.ErrNotFound, http.StatusNotFound, "NOT_FOUND", "El análisis no existe."},
	{domain.ErrUploadMissing, http.StatusConflict, "UPLOAD_MISSING",
		"No encontramos el video. Súbelo con la upload_url recibida antes de enviarlo."},
	{domain.ErrUploadTooLarge, http.StatusRequestEntityTooLarge, "UPLOAD_TOO_LARGE",
		"El video es demasiado grande. Recórtalo a un solo golpe (máx. 30 s)."},
	{domain.ErrUnsupportedMedia, http.StatusUnsupportedMediaType, "UNSUPPORTED_MEDIA",
		"El archivo no es un video. Usa MP4 o MOV."},
	{domain.ErrAnalysisBusy, http.StatusConflict, "ANALYSIS_BUSY",
		"El análisis se está procesando. Espera a que termine para eliminarlo."},
	{domain.ErrNotReady, http.StatusConflict, "NOT_READY",
		"El análisis aún no ha terminado: el video con esqueleto estará disponible al completarse."},
	{domain.ErrResourceNotFound, http.StatusNotFound, "NOT_FOUND", "El recurso no existe."},
	{domain.ErrAccountSuspended, http.StatusForbidden, "ACCOUNT_SUSPENDED",
		"Tu cuenta está suspendida. Contacta con el administrador."},
	{domain.ErrCampaignClosed, http.StatusConflict, "CAMPAIGN_CLOSED",
		"La campaña ya está cerrada: sus calificaciones son definitivas."},
	{domain.ErrNotARater, http.StatusForbidden, "NOT_A_RATER", "No eres evaluador de esta campaña."},
	{domain.ErrEvidenceInsufficient, http.StatusUnprocessableEntity, "VALIDATION_EVIDENCE_INSUFFICIENT", ""},
	// El motivo concreto (p. ej. "nadie valida sus propias propuestas") es útil y no sensible.
	{domain.ErrForbidden, http.StatusForbidden, "FORBIDDEN", ""},
	{domain.ErrConflict, http.StatusConflict, "CONFLICT", ""},
}

func writeError(c *gin.Context, logger *slog.Logger, err error) {
	for _, m := range errorMappings {
		if errors.Is(err, m.target) {
			msg := m.message
			if msg == "" {
				msg = err.Error()
			}
			c.JSON(m.status, gin.H{"error": errorBody{Code: m.code, Message: msg}})
			return
		}
	}
	logger.ErrorContext(c.Request.Context(), "error interno", "error", err, "path", c.FullPath())
	c.JSON(http.StatusInternalServerError, gin.H{"error": errorBody{
		Code: "INTERNAL", Message: "Error interno. Inténtalo de nuevo en unos minutos.",
	}})
}
