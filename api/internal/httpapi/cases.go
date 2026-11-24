package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"

	"tennisvision/api/internal/domain"
	"tennisvision/api/internal/service"
)

type CaseUseCases interface {
	List(ctx context.Context, f domain.CaseFilter) (service.Page, error)
	Get(ctx context.Context, id string) (service.CaseDetail, error)
}

type casesHandler struct {
	svc    CaseUseCases
	logger *slog.Logger
}

// Los DTO de casos no llevan user_id ni nada que identifique al dueño del video.
type caseSummary struct {
	ID          string    `json:"id"`
	StrokeType  *string   `json:"stroke_type"`
	CameraView  string    `json:"camera_view"`
	CreatedAt   time.Time `json:"created_at"`
	FindingIDs  []string  `json:"finding_rule_ids"`
	StrengthIDs []string  `json:"strength_rule_ids"`
}

type caseDetail struct {
	ID         string          `json:"id"`
	StrokeType *string         `json:"stroke_type"`
	Handedness string          `json:"handedness"`
	CameraView string          `json:"camera_view"`
	CreatedAt  time.Time       `json:"created_at"`
	Report     json.RawMessage `json:"report"`
	VideoURL   string          `json:"video_url"`
	PoseURL    string          `json:"pose_url"`
	ExpiresAt  time.Time       `json:"expires_at"`
}

type casePage struct {
	Items      []caseSummary `json:"items"`
	NextCursor *string       `json:"next_cursor"`
}

func (h *casesHandler) list(c *gin.Context) {
	limit := 0
	if raw := c.Query("limit"); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil {
			writeError(c, h.logger, fmt.Errorf("%w: limit debe ser un número", domain.ErrInvalidInput))
			return
		}
		limit = n
	}
	f, err := domain.NewCaseFilter(c.Query("stroke"), c.Query("rule_id"), c.Query("cursor"), limit)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	page, err := h.svc.List(c.Request.Context(), f)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	resp := casePage{Items: make([]caseSummary, 0, len(page.Items))}
	for _, a := range page.Items {
		findings, strengths := firedRules(a.Report)
		resp.Items = append(resp.Items, caseSummary{
			ID: a.ID, StrokeType: caseStroke(a), CameraView: string(a.Params.CameraView), CreatedAt: a.CreatedAt,
			FindingIDs: findings, StrengthIDs: strengths,
		})
	}
	if page.NextCursor != "" {
		resp.NextCursor = &page.NextCursor
	}
	c.JSON(http.StatusOK, resp)
}

func (h *casesHandler) get(c *gin.Context) {
	d, err := h.svc.Get(c.Request.Context(), c.Param("id"))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	a := d.Analysis
	c.JSON(http.StatusOK, caseDetail{
		ID: a.ID, StrokeType: caseStroke(a), Handedness: string(a.Params.Handedness),
		CameraView: string(a.Params.CameraView), CreatedAt: a.CreatedAt, Report: rawOrNull(a.Report),
		VideoURL: d.Media.VideoURL, PoseURL: d.Media.PoseURL, ExpiresAt: d.Media.ExpiresAt,
	})
}

type reportRules struct {
	StrokeType string `json:"stroke_type"`
	Findings   []struct {
		RuleID string `json:"rule_id"`
	} `json:"findings"`
	Strengths []struct {
		RuleID string `json:"rule_id"`
	} `json:"strengths"`
}

// caseStroke prefiere el golpe del reporte (el detectado) al declarado, que puede ser "auto".
func caseStroke(a domain.Analysis) *string {
	var r reportRules
	if json.Unmarshal(a.Report, &r) == nil && r.StrokeType != "" {
		return &r.StrokeType
	}
	return strokeOrNull(a.Params.StrokeType)
}

func firedRules(report []byte) (findings, strengths []string) {
	findings, strengths = []string{}, []string{}
	var r reportRules
	if json.Unmarshal(report, &r) != nil {
		return findings, strengths
	}
	for _, f := range r.Findings {
		findings = append(findings, f.RuleID)
	}
	for _, s := range r.Strengths {
		strengths = append(strengths, s.RuleID)
	}
	return findings, strengths
}
