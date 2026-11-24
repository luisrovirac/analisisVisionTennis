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

type KnowledgeUseCases interface {
	Catalog(ctx context.Context, stroke domain.StrokeType) ([]domain.MetricInfo, error)
	ActiveRelease(ctx context.Context, stroke domain.StrokeType) (domain.Release, error)
	Releases(ctx context.Context, stroke domain.StrokeType) ([]domain.Release, error)
	Proposals(ctx context.Context, f service.ProposalFilter) ([]domain.Proposal, error)
	Proposal(ctx context.Context, id string) (service.ProposalDetail, error)
	Preview(ctx context.Context, stroke domain.StrokeType, rule domain.Rule) (service.RuleImpact, int, error)
	CreateProposal(ctx context.Context, author domain.User, in domain.ProposalInput) (domain.Proposal, error)
	UpdateProposal(ctx context.Context, author domain.User, id string, in domain.ProposalInput) (domain.Proposal, error)
	Submit(ctx context.Context, author domain.User, id string) (domain.Proposal, error)
	Withdraw(ctx context.Context, author domain.User, id string) (domain.Proposal, error)
	AddComment(ctx context.Context, author domain.User, proposalID, body string) (domain.Comment, error)
	CreateMetricRequest(ctx context.Context, author domain.User, m domain.MetricRequest) (domain.MetricRequest, error)
	MetricRequests(ctx context.Context) ([]domain.MetricRequest, error)
	Approve(ctx context.Context, validator domain.User, id, note string) (domain.Release, error)
	Reject(ctx context.Context, validator domain.User, id, note string) (domain.Proposal, error)
	RequestChanges(ctx context.Context, validator domain.User, id, note string) (domain.Proposal, error)
	Activate(ctx context.Context, actor domain.User, releaseID string) (domain.Release, error)
}

type knowledgeHandler struct {
	svc    KnowledgeUseCases
	logger *slog.Logger
}

// --- DTOs ---

type releaseDTO struct {
	ID            string                  `json:"id"`
	Stroke        domain.StrokeType       `json:"stroke"`
	Version       string                  `json:"version"`
	Changelog     string                  `json:"changelog"`
	Entries       []domain.ChangelogEntry `json:"changelog_entries"`
	Bump          *domain.Bump            `json:"bump"`
	ProposedBy    string                  `json:"proposed_by_name,omitempty"`
	ValidatedBy   string                  `json:"validated_by_name,omitempty"`
	ProposalID    *string                 `json:"proposal_id"`
	Active        bool                    `json:"active"`
	Status        string                  `json:"status"`
	InvalidReason *string                 `json:"invalid_reason"`
	CreatedAt     time.Time               `json:"created_at"`
	KnowledgeBase *domain.KnowledgeBase   `json:"knowledge_base,omitempty"`
}

func toReleaseDTO(r domain.Release, withKB bool) releaseDTO {
	entries := r.Entries
	if entries == nil {
		entries = []domain.ChangelogEntry{}
	}
	dto := releaseDTO{ID: r.ID, Stroke: r.Stroke, Version: r.Version, Changelog: r.Changelog, Entries: entries, Bump: r.Bump,
		ProposedBy: r.ProposerName, ValidatedBy: r.ValidatorName, ProposalID: r.ProposalID,
		Active: r.Active, Status: r.Status, InvalidReason: r.InvalidReason, CreatedAt: r.CreatedAt}
	if withKB {
		kb := r.KB
		dto.KnowledgeBase = &kb
	}
	return dto
}

type proposalDTO struct {
	ID            string                `json:"id"`
	AuthorID      string                `json:"author_id"`
	AuthorName    string                `json:"author_name"`
	Stroke        domain.StrokeType     `json:"stroke"`
	Title         string                `json:"title"`
	Rationale     string                `json:"rationale"`
	Evidence      string                `json:"evidence"`
	Changes       []domain.Change       `json:"changes"`
	Status        domain.ProposalStatus `json:"status"`
	BaseReleaseID *string               `json:"base_release_id"`
	DecidedBy     *string               `json:"decided_by"`
	DeciderName   string                `json:"decider_name"`
	DecidedAt     *time.Time            `json:"decided_at"`
	DecisionNote  string                `json:"decision_note"`
	ReleaseID     *string               `json:"release_id"`
	CreatedAt     time.Time             `json:"created_at"`
	UpdatedAt     time.Time             `json:"updated_at"`
}

func toProposalDTO(p domain.Proposal) proposalDTO {
	return proposalDTO{ID: p.ID, AuthorID: p.AuthorID, AuthorName: p.AuthorName, Stroke: p.Stroke, Title: p.Title,
		Rationale: p.Rationale, Evidence: p.Evidence, Changes: p.Changes, Status: p.Status, BaseReleaseID: p.BaseReleaseID,
		DecidedBy: p.DecidedBy, DeciderName: p.DeciderName, DecidedAt: p.DecidedAt, DecisionNote: p.DecisionNote,
		ReleaseID: p.ReleaseID, CreatedAt: p.CreatedAt, UpdatedAt: p.UpdatedAt}
}

type commentDTO struct {
	ID         string    `json:"id"`
	AuthorID   string    `json:"author_id"`
	AuthorName string    `json:"author_name"`
	Body       string    `json:"body"`
	CreatedAt  time.Time `json:"created_at"`
}

func toCommentDTO(c domain.Comment) commentDTO {
	return commentDTO{ID: c.ID, AuthorID: c.AuthorID, AuthorName: c.AuthorName, Body: c.Body, CreatedAt: c.CreatedAt}
}

type metricRequestDTO struct {
	ID          string            `json:"id"`
	AuthorName  string            `json:"author_name"`
	Stroke      domain.StrokeType `json:"stroke"`
	Name        string            `json:"name"`
	Description string            `json:"description"`
	Status      string            `json:"status"`
	CreatedAt   time.Time         `json:"created_at"`
}

func toMetricRequestDTO(m domain.MetricRequest) metricRequestDTO {
	return metricRequestDTO{ID: m.ID, AuthorName: m.AuthorName, Stroke: m.Stroke, Name: m.Name,
		Description: m.Description, Status: m.Status, CreatedAt: m.CreatedAt}
}

// --- Lectura (expertos, validadores y admins) ---

func (h *knowledgeHandler) catalog(c *gin.Context) {
	items, err := h.svc.Catalog(c.Request.Context(), domain.StrokeType(c.Query("stroke")))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	if items == nil {
		items = []domain.MetricInfo{}
	}
	c.JSON(http.StatusOK, gin.H{"items": items})
}

func (h *knowledgeHandler) active(c *gin.Context) {
	r, err := h.svc.ActiveRelease(c.Request.Context(), domain.StrokeType(c.Param("stroke")))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, toReleaseDTO(r, true))
}

func (h *knowledgeHandler) releases(c *gin.Context) {
	items, err := h.svc.Releases(c.Request.Context(), domain.StrokeType(c.Query("stroke")))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	out := make([]releaseDTO, 0, len(items))
	for _, r := range items {
		out = append(out, toReleaseDTO(r, false))
	}
	c.JSON(http.StatusOK, gin.H{"items": out})
}

func (h *knowledgeHandler) proposals(c *gin.Context) {
	f := service.ProposalFilter{Status: domain.ProposalStatus(c.Query("status"))}
	if c.Query("mine") == "true" {
		f.AuthorID = ownerID(c)
	}
	items, err := h.svc.Proposals(c.Request.Context(), f)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	out := make([]proposalDTO, 0, len(items))
	for _, p := range items {
		out = append(out, toProposalDTO(p))
	}
	c.JSON(http.StatusOK, gin.H{"items": out})
}

func (h *knowledgeHandler) proposal(c *gin.Context) {
	d, err := h.svc.Proposal(c.Request.Context(), c.Param("id"))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	comments := make([]commentDTO, 0, len(d.Comments))
	for _, cm := range d.Comments {
		comments = append(comments, toCommentDTO(cm))
	}
	var active *releaseDTO
	if d.ActiveRelease != nil {
		dto := toReleaseDTO(*d.ActiveRelease, true)
		active = &dto
	}
	impacts := d.Impacts
	if impacts == nil {
		impacts = []service.RuleImpact{}
	}
	c.JSON(http.StatusOK, gin.H{
		"proposal": toProposalDTO(d.Proposal), "comments": comments, "active_release": active,
		"impacts": impacts, "problems": d.Problems, "stale": d.Stale,
	})
}

func (h *knowledgeHandler) preview(c *gin.Context) {
	var req struct {
		Stroke domain.StrokeType `json:"stroke"`
		Rule   domain.Rule       `json:"rule"`
	}
	if err := decodeStrict(c, &req); err != nil {
		writeError(c, h.logger, err)
		return
	}
	impact, samples, err := h.svc.Preview(c.Request.Context(), req.Stroke, req.Rule)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"impact": impact, "samples": samples})
}

func (h *knowledgeHandler) comment(c *gin.Context) {
	var req struct {
		Body string `json:"body"`
	}
	if err := decodeStrict(c, &req); err != nil {
		writeError(c, h.logger, err)
		return
	}
	cm, err := h.svc.AddComment(c.Request.Context(), currentUser(c), c.Param("id"), req.Body)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusCreated, toCommentDTO(cm))
}

func (h *knowledgeHandler) metricRequests(c *gin.Context) {
	items, err := h.svc.MetricRequests(c.Request.Context())
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	out := make([]metricRequestDTO, 0, len(items))
	for _, m := range items {
		out = append(out, toMetricRequestDTO(m))
	}
	c.JSON(http.StatusOK, gin.H{"items": out})
}

// --- Experto ---

func (h *knowledgeHandler) createProposal(c *gin.Context) {
	var in domain.ProposalInput
	if err := decodeStrict(c, &in); err != nil {
		writeError(c, h.logger, err)
		return
	}
	p, err := h.svc.CreateProposal(c.Request.Context(), currentUser(c), in)
	h.respondProposal(c, p, err, http.StatusCreated)
}

func (h *knowledgeHandler) updateProposal(c *gin.Context) {
	var in domain.ProposalInput
	if err := decodeStrict(c, &in); err != nil {
		writeError(c, h.logger, err)
		return
	}
	p, err := h.svc.UpdateProposal(c.Request.Context(), currentUser(c), c.Param("id"), in)
	h.respondProposal(c, p, err, http.StatusOK)
}

func (h *knowledgeHandler) submit(c *gin.Context) {
	p, err := h.svc.Submit(c.Request.Context(), currentUser(c), c.Param("id"))
	h.respondProposal(c, p, err, http.StatusOK)
}

func (h *knowledgeHandler) withdraw(c *gin.Context) {
	p, err := h.svc.Withdraw(c.Request.Context(), currentUser(c), c.Param("id"))
	h.respondProposal(c, p, err, http.StatusOK)
}

func (h *knowledgeHandler) createMetricRequest(c *gin.Context) {
	var req struct {
		Stroke      domain.StrokeType `json:"stroke"`
		Name        string            `json:"name"`
		Description string            `json:"description"`
	}
	if err := decodeStrict(c, &req); err != nil {
		writeError(c, h.logger, err)
		return
	}
	m, err := h.svc.CreateMetricRequest(c.Request.Context(), currentUser(c),
		domain.MetricRequest{Stroke: req.Stroke, Name: req.Name, Description: req.Description})
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusCreated, toMetricRequestDTO(m))
}

// --- Validador ---

type decisionRequest struct {
	Note string `json:"note"`
}

func (h *knowledgeHandler) approve(c *gin.Context) {
	var req decisionRequest
	if err := decodeStrict(c, &req); err != nil {
		writeError(c, h.logger, err)
		return
	}
	r, err := h.svc.Approve(c.Request.Context(), currentUser(c), c.Param("id"), req.Note)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, toReleaseDTO(r, false))
}

func (h *knowledgeHandler) reject(c *gin.Context) {
	h.decide(c, h.svc.Reject)
}

func (h *knowledgeHandler) requestChanges(c *gin.Context) {
	h.decide(c, h.svc.RequestChanges)
}

func (h *knowledgeHandler) decide(c *gin.Context, action func(context.Context, domain.User, string, string) (domain.Proposal, error)) {
	var req decisionRequest
	if err := decodeStrict(c, &req); err != nil {
		writeError(c, h.logger, err)
		return
	}
	p, err := action(c.Request.Context(), currentUser(c), c.Param("id"), req.Note)
	h.respondProposal(c, p, err, http.StatusOK)
}

func (h *knowledgeHandler) activate(c *gin.Context) {
	r, err := h.svc.Activate(c.Request.Context(), currentUser(c), c.Param("id"))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, toReleaseDTO(r, false))
}

func (h *knowledgeHandler) respondProposal(c *gin.Context, p domain.Proposal, err error, status int) {
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(status, toProposalDTO(p))
}
