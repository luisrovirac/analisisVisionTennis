package httpapi

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"

	"github.com/gin-gonic/gin"

	"tennisvision/api/internal/domain"
)

type PreferenceUseCases interface {
	Get(ctx context.Context, userID string) (domain.Preferences, error)
	Update(ctx context.Context, userID string, p domain.Preferences) (domain.Preferences, error)
}

type preferencesDTO struct {
	Handedness domain.Handedness `json:"handedness"`
}

type preferencesHandler struct {
	svc    PreferenceUseCases
	logger *slog.Logger
}

func (h *preferencesHandler) get(c *gin.Context) {
	p, err := h.svc.Get(c.Request.Context(), ownerID(c))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, preferencesDTO(p))
}

func (h *preferencesHandler) put(c *gin.Context) {
	var req preferencesDTO
	if err := c.ShouldBindJSON(&req); err != nil {
		writeError(c, h.logger, fmt.Errorf("%w: cuerpo JSON inválido", domain.ErrInvalidInput))
		return
	}
	p, err := h.svc.Update(c.Request.Context(), ownerID(c), domain.Preferences(req))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, preferencesDTO(p))
}
