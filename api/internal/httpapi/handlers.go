// Package httpapi expone los casos de uso por HTTP. Los handlers son delgados: traducen y delegan.
package httpapi

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"

	"tennisvision/api/internal/domain"
	"tennisvision/api/internal/service"
)

type AnalysisUseCases interface {
	Create(ctx context.Context, ownerID string, p domain.AnalysisParams) (service.UploadTicket, error)
	Submit(ctx context.Context, ownerID, id string) (domain.Analysis, error)
	Get(ctx context.Context, ownerID, id string) (domain.Analysis, error)
	List(ctx context.Context, ownerID string, q domain.ListQuery) (service.Page, error)
	Delete(ctx context.Context, ownerID, id string) error
	SetExpertReview(ctx context.Context, ownerID, id string, allowed bool) (domain.Analysis, error)
}

type analysisHandler struct {
	svc    AnalysisUseCases
	logger *slog.Logger
}

func (h *analysisHandler) create(c *gin.Context) {
	var req createAnalysisRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		writeError(c, h.logger, fmt.Errorf("%w: cuerpo JSON inválido", domain.ErrInvalidInput))
		return
	}
	ticket, err := h.svc.Create(c.Request.Context(), ownerID(c), req.toParams())
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusCreated, toTicketResponse(ticket))
}

func (h *analysisHandler) submit(c *gin.Context) {
	a, err := h.svc.Submit(c.Request.Context(), ownerID(c), c.Param("id"))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusAccepted, toAnalysisResponse(a))
}

func (h *analysisHandler) get(c *gin.Context) {
	a, err := h.svc.Get(c.Request.Context(), ownerID(c), c.Param("id"))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, toAnalysisResponse(a))
}

func (h *analysisHandler) remove(c *gin.Context) {
	if err := h.svc.Delete(c.Request.Context(), ownerID(c), c.Param("id")); err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.Status(http.StatusNoContent)
}

// update solo permite cambiar el checkbox de revisión experta.
func (h *analysisHandler) update(c *gin.Context) {
	var req updateAnalysisRequest
	if err := c.ShouldBindJSON(&req); err != nil || req.ExpertReviewAllowed == nil {
		writeError(c, h.logger, fmt.Errorf("%w: envía {\"expert_review_allowed\": true|false}", domain.ErrInvalidInput))
		return
	}
	a, err := h.svc.SetExpertReview(c.Request.Context(), ownerID(c), c.Param("id"), *req.ExpertReviewAllowed)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, toAnalysisResponse(a))
}

func (h *analysisHandler) list(c *gin.Context) {
	q, err := parseListQuery(c)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	page, err := h.svc.List(c.Request.Context(), ownerID(c), q)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, toPageResponse(page))
}

func parseListQuery(c *gin.Context) (domain.ListQuery, error) {
	limit := 0
	if raw := c.Query("limit"); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil {
			return domain.ListQuery{}, fmt.Errorf("%w: limit debe ser un número", domain.ErrInvalidInput)
		}
		limit = n
	}
	return domain.NewListQuery(c.Query("cursor"), limit)
}
