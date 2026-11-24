package httpapi

import (
	"context"
	"log/slog"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"tennisvision/api/internal/domain"
	"tennisvision/api/internal/service"
)

type ValidationUseCases interface {
	AddToCorpus(ctx context.Context, actor domain.User, analysisID, note string) (domain.CorpusEntry, error)
	RemoveFromCorpus(ctx context.Context, actor domain.User, analysisID string) error
	Corpus(ctx context.Context, stroke domain.StrokeType) ([]domain.CorpusEntry, error)
	Professionals(ctx context.Context) ([]service.Professional, error)

	SaveFreeReview(ctx context.Context, reviewer domain.User, caseID string, in domain.ReviewInput) (domain.CaseReview, error)
	CaseReviews(ctx context.Context, viewer domain.User, caseID string) (service.CaseReviews, error)

	CreateCampaign(ctx context.Context, actor domain.User, in domain.CampaignInput) (domain.Campaign, error)
	Campaigns(ctx context.Context, stroke domain.StrokeType) ([]domain.Campaign, error)
	CampaignDetail(ctx context.Context, viewer domain.User, id string) (service.CampaignDetail, error)
	CampaignCase(ctx context.Context, rater domain.User, campaignID, caseID string) (service.BlindCase, error)
	SaveCampaignReview(ctx context.Context, rater domain.User, campaignID, caseID string, in domain.ReviewInput) (domain.CaseReview, error)
	Advance(ctx context.Context, actor domain.User, id string, force bool) (domain.Campaign, error)
	Close(ctx context.Context, actor domain.User, id string) (domain.Campaign, error)

	Calibrate(ctx context.Context, req service.CalibrationRequest) (service.CalibrationResult, error)
	Quality(ctx context.Context, stroke domain.StrokeType) (service.QualityReport, error)
}

type validationHandler struct {
	svc    ValidationUseCases
	logger *slog.Logger
}

// --- DTOs (sin user_id del dueño del video; los revisores solo se ven por id interno) ---

type corpusDTO struct {
	AnalysisID  string            `json:"analysis_id"`
	Stroke      domain.StrokeType `json:"stroke"`
	Split       string            `json:"split"`
	Note        string            `json:"note"`
	AddedAt     time.Time         `json:"added_at"`
	GroundTruth int               `json:"ground_truth_rules"`
}

type reviewDTO struct {
	ID          string              `json:"id"`
	AnalysisID  string              `json:"analysis_id"`
	CampaignID  *string             `json:"campaign_id"`
	Round       *int                `json:"round"`
	PlayerLevel *string             `json:"player_level"`
	Notes       string              `json:"notes"`
	KBVersion   *string             `json:"kb_version"`
	Items       []domain.ReviewItem `json:"items"`
	SubmittedAt time.Time           `json:"submitted_at"`
}

func toReviewDTO(r *domain.CaseReview) *reviewDTO {
	if r == nil {
		return nil
	}
	items := r.Items
	if items == nil {
		items = []domain.ReviewItem{}
	}
	return &reviewDTO{ID: r.ID, AnalysisID: r.AnalysisID, CampaignID: r.CampaignID, Round: r.Round,
		PlayerLevel: r.PlayerLevel, Notes: r.Notes, KBVersion: r.KBVersion, Items: items, SubmittedAt: r.SubmittedAt}
}

type campaignDTO struct {
	ID           string                  `json:"id"`
	Stroke       domain.StrokeType       `json:"stroke"`
	Title        string                  `json:"title"`
	RuleIDs      []string                `json:"rule_ids"`
	Status       string                  `json:"status"`
	CurrentRound int                     `json:"current_round"`
	MaxRounds    int                     `json:"max_rounds"`
	ConsensusPct float64                 `json:"consensus_pct"`
	MinRaters    int                     `json:"min_raters"`
	Cases        int                     `json:"cases"`
	Raters       int                     `json:"raters"`
	Results      *domain.CampaignResults `json:"results"`
	CreatedAt    time.Time               `json:"created_at"`
	ClosedAt     *time.Time              `json:"closed_at"`
	CaseIDs      []string                `json:"case_ids,omitempty"`
}

func toCampaignDTO(c domain.Campaign, withCases bool) campaignDTO {
	dto := campaignDTO{ID: c.ID, Stroke: c.Stroke, Title: c.Title, RuleIDs: c.RuleIDs, Status: c.Status,
		CurrentRound: c.CurrentRound, MaxRounds: c.MaxRounds, ConsensusPct: c.ConsensusPct, MinRaters: c.MinRaters,
		Cases: len(c.CaseIDs), Raters: len(c.RaterIDs), Results: c.Results, CreatedAt: c.CreatedAt, ClosedAt: c.ClosedAt}
	if withCases {
		dto.CaseIDs = c.CaseIDs
	}
	return dto
}

// --- Corpus ---

func (h *validationHandler) corpus(c *gin.Context) {
	items, err := h.svc.Corpus(c.Request.Context(), domain.StrokeType(c.Query("stroke")))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	out := make([]corpusDTO, 0, len(items))
	for _, e := range items {
		out = append(out, corpusDTO{AnalysisID: e.AnalysisID, Stroke: e.Stroke, Split: e.Split, Note: e.Note,
			AddedAt: e.AddedAt, GroundTruth: e.GroundTruth})
	}
	c.JSON(http.StatusOK, gin.H{"items": out})
}

func (h *validationHandler) addToCorpus(c *gin.Context) {
	var req struct {
		AnalysisID string `json:"analysis_id"`
		Note       string `json:"note"`
	}
	if err := decodeStrict(c, &req); err != nil {
		writeError(c, h.logger, err)
		return
	}
	e, err := h.svc.AddToCorpus(c.Request.Context(), currentUser(c), req.AnalysisID, req.Note)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusCreated, corpusDTO{AnalysisID: e.AnalysisID, Stroke: e.Stroke, Split: e.Split, Note: e.Note, AddedAt: e.AddedAt})
}

func (h *validationHandler) removeFromCorpus(c *gin.Context) {
	if err := h.svc.RemoveFromCorpus(c.Request.Context(), currentUser(c), c.Param("id")); err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.Status(http.StatusNoContent)
}

func (h *validationHandler) professionals(c *gin.Context) {
	items, err := h.svc.Professionals(c.Request.Context())
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	if items == nil {
		items = []service.Professional{}
	}
	c.JSON(http.StatusOK, gin.H{"items": items})
}

// --- Revisión libre ---

func (h *validationHandler) saveFreeReview(c *gin.Context) {
	var in domain.ReviewInput
	if err := decodeStrict(c, &in); err != nil {
		writeError(c, h.logger, err)
		return
	}
	r, err := h.svc.SaveFreeReview(c.Request.Context(), currentUser(c), c.Param("id"), in)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, toReviewDTO(&r))
}

func (h *validationHandler) caseReviews(c *gin.Context) {
	d, err := h.svc.CaseReviews(c.Request.Context(), currentUser(c), c.Param("id"))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	rules := d.Rules
	if rules == nil {
		rules = []service.RuleTally{}
	}
	c.JSON(http.StatusOK, gin.H{"mine": toReviewDTO(d.Mine), "others": d.Others, "rules": rules})
}

// --- Campañas ---

func (h *validationHandler) campaigns(c *gin.Context) {
	items, err := h.svc.Campaigns(c.Request.Context(), domain.StrokeType(c.Query("stroke")))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	out := make([]campaignDTO, 0, len(items))
	for _, cp := range items {
		out = append(out, toCampaignDTO(cp, false))
	}
	c.JSON(http.StatusOK, gin.H{"items": out})
}

func (h *validationHandler) createCampaign(c *gin.Context) {
	var in domain.CampaignInput
	if err := decodeStrict(c, &in); err != nil {
		writeError(c, h.logger, err)
		return
	}
	cp, err := h.svc.CreateCampaign(c.Request.Context(), currentUser(c), in)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusCreated, toCampaignDTO(cp, true))
}

func (h *validationHandler) campaign(c *gin.Context) {
	d, err := h.svc.CampaignDetail(c.Request.Context(), currentUser(c), c.Param("id"))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	progress, rounds, done := d.Progress, d.Rounds, d.MyDone
	if progress == nil {
		progress = []service.RaterProgress{}
	}
	if rounds == nil {
		rounds = []service.RoundStats{}
	}
	if done == nil {
		done = []string{}
	}
	c.JSON(http.StatusOK, gin.H{"campaign": toCampaignDTO(d.Campaign, true), "is_rater": d.IsRater,
		"progress": progress, "rounds": rounds, "my_done": done})
}

func (h *validationHandler) campaignCase(c *gin.Context) {
	b, err := h.svc.CampaignCase(c.Request.Context(), currentUser(c), c.Param("id"), c.Param("caseId"))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	rules, feedback := b.Rules, b.Feedback
	if rules == nil {
		rules = []service.CampaignRule{}
	}
	if feedback == nil {
		feedback = []service.RuleFeedback{}
	}
	a := b.Analysis
	// Vista ciega: ni hallazgos ni métricas del sistema; solo el video, el esqueleto y las fases.
	c.JSON(http.StatusOK, gin.H{
		"campaign": toCampaignDTO(b.Campaign, false), "case_id": a.ID, "stroke_type": caseStroke(a),
		"handedness": a.Params.Handedness, "camera_view": a.Params.CameraView, "segment": rawOrNull(b.Segment),
		"video_url": b.Media.VideoURL, "pose_url": b.Media.PoseURL, "expires_at": b.Media.ExpiresAt,
		"rules": rules, "mine": toReviewDTO(b.Mine), "feedback": feedback,
	})
}

func (h *validationHandler) saveCampaignReview(c *gin.Context) {
	var in domain.ReviewInput
	if err := decodeStrict(c, &in); err != nil {
		writeError(c, h.logger, err)
		return
	}
	r, err := h.svc.SaveCampaignReview(c.Request.Context(), currentUser(c), c.Param("id"), c.Param("caseId"), in)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, toReviewDTO(&r))
}

func (h *validationHandler) advance(c *gin.Context) {
	var req struct {
		Force bool `json:"force"`
	}
	if err := decodeStrict(c, &req); err != nil {
		writeError(c, h.logger, err)
		return
	}
	cp, err := h.svc.Advance(c.Request.Context(), currentUser(c), c.Param("id"), req.Force)
	h.respondCampaign(c, cp, err)
}

func (h *validationHandler) close(c *gin.Context) {
	cp, err := h.svc.Close(c.Request.Context(), currentUser(c), c.Param("id"))
	h.respondCampaign(c, cp, err)
}

func (h *validationHandler) respondCampaign(c *gin.Context, cp domain.Campaign, err error) {
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, toCampaignDTO(cp, true))
}

// --- Calibración y calidad ---

func (h *validationHandler) calibrate(c *gin.Context) {
	var req service.CalibrationRequest
	if err := decodeStrict(c, &req); err != nil {
		writeError(c, h.logger, err)
		return
	}
	res, err := h.svc.Calibrate(c.Request.Context(), req)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, res)
}

func (h *validationHandler) quality(c *gin.Context) {
	q, err := h.svc.Quality(c.Request.Context(), domain.StrokeType(c.Query("stroke")))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	if q.Rules == nil {
		q.Rules = []service.RuleQuality{}
	}
	c.JSON(http.StatusOK, q)
}
