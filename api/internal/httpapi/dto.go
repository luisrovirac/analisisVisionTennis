package httpapi

import (
	"encoding/json"
	"time"

	"tennisvision/api/internal/domain"
	"tennisvision/api/internal/service"
)

type createAnalysisRequest struct {
	StrokeType string `json:"stroke_type"`
	Handedness string `json:"handedness"`
	CameraView string `json:"camera_view"`
	// Ausente = true: el checkbox "Permitir que expertos vean este video" viene marcado.
	ExpertReviewAllowed *bool `json:"expert_review_allowed"`
}

func (r createAnalysisRequest) toParams() domain.AnalysisParams {
	return domain.AnalysisParams{
		StrokeType:          domain.StrokeType(r.StrokeType),
		Handedness:          domain.Handedness(r.Handedness),
		CameraView:          domain.CameraView(r.CameraView),
		ExpertReviewAllowed: r.ExpertReviewAllowed == nil || *r.ExpertReviewAllowed,
	}
}

type updateAnalysisRequest struct {
	ExpertReviewAllowed *bool `json:"expert_review_allowed"`
}

type errorBody struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

type analysisResponse struct {
	ID         string        `json:"id"`
	Status     domain.Status `json:"status"`
	StrokeType *string       `json:"stroke_type"` // null = pendiente de detectar
	Handedness string        `json:"handedness"`
	CameraView string        `json:"camera_view"`
	// ExpertReviewAllowed: el dueño permite que expertos vean el video (control de calidad de reglas).
	ExpertReviewAllowed bool            `json:"expert_review_allowed"`
	Error               *errorBody      `json:"error"`
	Report              json.RawMessage `json:"report"`
	CreatedAt           time.Time       `json:"created_at"`
	UpdatedAt           time.Time       `json:"updated_at"`
}

type mediaResponse struct {
	VideoURL  string    `json:"video_url"`
	PoseURL   string    `json:"pose_url"`
	ExpiresAt time.Time `json:"expires_at"`
}

func toMediaResponse(m domain.MediaLinks) mediaResponse {
	return mediaResponse{VideoURL: m.VideoURL, PoseURL: m.PoseURL, ExpiresAt: m.ExpiresAt}
}

type uploadTicketResponse struct {
	Analysis  analysisResponse `json:"analysis"`
	UploadURL string           `json:"upload_url"`
	ExpiresAt time.Time        `json:"expires_at"`
}

func toAnalysisResponse(a domain.Analysis) analysisResponse {
	return analysisResponse{
		ID:                  a.ID,
		Status:              a.Status,
		StrokeType:          strokeOrNull(a.Params.StrokeType),
		Handedness:          string(a.Params.Handedness),
		CameraView:          string(a.Params.CameraView),
		ExpertReviewAllowed: a.Params.ExpertReviewAllowed,
		Error:               toErrorBody(a),
		Report:              rawOrNull(a.Report),
		CreatedAt:           a.CreatedAt,
		UpdatedAt:           a.UpdatedAt,
	}
}

func strokeOrNull(s domain.StrokeType) *string {
	if s == domain.StrokeAuto {
		return nil
	}
	v := string(s)
	return &v
}

func toErrorBody(a domain.Analysis) *errorBody {
	if a.ErrorCode == nil {
		return nil
	}
	body := &errorBody{Code: *a.ErrorCode}
	if a.ErrorMessage != nil {
		body.Message = *a.ErrorMessage
	}
	return body
}

func rawOrNull(b []byte) json.RawMessage {
	if len(b) == 0 {
		return json.RawMessage("null")
	}
	return b
}

func toTicketResponse(t service.UploadTicket) uploadTicketResponse {
	return uploadTicketResponse{
		Analysis:  toAnalysisResponse(t.Analysis),
		UploadURL: t.UploadURL,
		ExpiresAt: t.ExpiresAt,
	}
}

// analysisSummary es la vista de listado: sin el reporte completo, que puede pesar decenas de KB.
type analysisSummary struct {
	ID         string        `json:"id"`
	Status     domain.Status `json:"status"`
	StrokeType *string       `json:"stroke_type"`
	Error      *errorBody    `json:"error"`
	CreatedAt  time.Time     `json:"created_at"`
	UpdatedAt  time.Time     `json:"updated_at"`
}

type pageResponse struct {
	Items      []analysisSummary `json:"items"`
	NextCursor *string           `json:"next_cursor"`
}

func toPageResponse(p service.Page) pageResponse {
	items := make([]analysisSummary, 0, len(p.Items))
	for _, a := range p.Items {
		items = append(items, analysisSummary{
			ID: a.ID, Status: a.Status, StrokeType: strokeOrNull(a.Params.StrokeType),
			Error: toErrorBody(a), CreatedAt: a.CreatedAt, UpdatedAt: a.UpdatedAt,
		})
	}
	resp := pageResponse{Items: items}
	if p.NextCursor != "" {
		resp.NextCursor = &p.NextCursor
	}
	return resp
}

// ClientAuthConfig es lo que el cliente web necesita para iniciar el login (datos públicos).
type ClientAuthConfig struct {
	Enabled  bool   `json:"enabled"`
	Issuer   string `json:"issuer,omitempty"`
	ClientID string `json:"client_id,omitempty"`
}
