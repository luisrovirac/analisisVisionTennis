package service

import (
	"context"
	"fmt"
	"slices"
	"strings"
	"time"

	"tennisvision/api/internal/domain"
)

// ProposalFilter filtra el listado de propuestas. Vacío = todas.
type ProposalFilter struct {
	Status   domain.ProposalStatus
	AuthorID string
	Limit    int
}

// ApproveFunc construye, dentro de la transacción de aprobación, la base de conocimiento resultante.
type ApproveFunc func(active domain.Release, p domain.Proposal, catalog []domain.MetricInfo) (domain.KnowledgeBase, string, error)

type KnowledgeRepository interface {
	Catalog(ctx context.Context, stroke domain.StrokeType) ([]domain.MetricInfo, error)
	ActiveRelease(ctx context.Context, stroke domain.StrokeType) (domain.Release, error)
	Release(ctx context.Context, id string) (domain.Release, error)
	Releases(ctx context.Context, stroke domain.StrokeType, limit int) ([]domain.Release, error)

	CreateProposal(ctx context.Context, p domain.Proposal) (domain.Proposal, error)
	Proposal(ctx context.Context, id string) (domain.Proposal, error)
	Proposals(ctx context.Context, f ProposalFilter) ([]domain.Proposal, error)
	// UpdateProposal guarda la propuesta solo si su estado actual está en `from` (concurrencia optimista).
	UpdateProposal(ctx context.Context, p domain.Proposal, from ...domain.ProposalStatus) (bool, error)
	// Decide registra el rechazo o la petición de cambios de un validador (solo desde `submitted`).
	Decide(ctx context.Context, id, deciderID string, to domain.ProposalStatus, note string) (bool, error)
	// Approve bloquea la propuesta y la versión activa, construye la nueva base con build y publica.
	Approve(ctx context.Context, id, validatorID, note string, build ApproveFunc) (domain.Release, error)
	// Activate reactiva una versión válida anterior (rollback).
	Activate(ctx context.Context, releaseID, actorID string) (domain.Release, error)

	AddComment(ctx context.Context, c domain.Comment) (domain.Comment, error)
	Comments(ctx context.Context, proposalID string) ([]domain.Comment, error)

	// RecentSamples: métricas de los últimos análisis completados cuyo dueño permitió la revisión experta.
	RecentSamples(ctx context.Context, stroke domain.StrokeType, limit int) ([]domain.MetricSample, error)

	CreateMetricRequest(ctx context.Context, m domain.MetricRequest) (domain.MetricRequest, error)
	MetricRequests(ctx context.Context, limit int) ([]domain.MetricRequest, error)

	Record(ctx context.Context, actorID *string, action, target string, details any) error
}

type KnowledgeConfig struct {
	MinMetricConfidence float64 // el mismo umbral que usa el worker (MIN_METRIC_CONFIDENCE)
	PreviewSampleSize   int     // cuántos análisis recientes usa la previsualización
}

const knowledgeListLimit = 100

// KnowledgeService implementa el flujo experto → validador → versión publicada.
type KnowledgeService struct {
	repo KnowledgeRepository
	cfg  KnowledgeConfig
	now  func() time.Time
}

func NewKnowledge(repo KnowledgeRepository, cfg KnowledgeConfig, now func() time.Time) *KnowledgeService {
	if cfg.PreviewSampleSize <= 0 {
		cfg.PreviewSampleSize = 500
	}
	return &KnowledgeService{repo: repo, cfg: cfg, now: now}
}

func (s *KnowledgeService) Catalog(ctx context.Context, stroke domain.StrokeType) ([]domain.MetricInfo, error) {
	return s.repo.Catalog(ctx, stroke)
}

func (s *KnowledgeService) ActiveRelease(ctx context.Context, stroke domain.StrokeType) (domain.Release, error) {
	return s.repo.ActiveRelease(ctx, stroke)
}

func (s *KnowledgeService) Releases(ctx context.Context, stroke domain.StrokeType) ([]domain.Release, error) {
	return s.repo.Releases(ctx, stroke, knowledgeListLimit)
}

func (s *KnowledgeService) Proposals(ctx context.Context, f ProposalFilter) ([]domain.Proposal, error) {
	f.Limit = knowledgeListLimit
	return s.repo.Proposals(ctx, f)
}

// ProposalDetail es lo que ve el validador para decidir.
type ProposalDetail struct {
	Proposal      domain.Proposal
	Comments      []domain.Comment
	ActiveRelease *domain.Release // versión activa actual (para el diff)
	Impacts       []RuleImpact    // efecto de cada regla editada sobre análisis reales
	Problems      string          // vacío = aplicable a la versión activa
	Stale         bool            // la versión activa ya no es sobre la que se envió
}

// RuleImpact compara antes/después en los mismos análisis recientes.
type RuleImpact struct {
	RuleID string            `json:"rule_id"`
	Before *domain.RuleCount `json:"before"` // nil = regla nueva
	After  *domain.RuleCount `json:"after"`  // nil = regla retirada
}

func (s *KnowledgeService) Proposal(ctx context.Context, id string) (ProposalDetail, error) {
	p, err := s.getProposal(ctx, id)
	if err != nil {
		return ProposalDetail{}, err
	}
	comments, err := s.repo.Comments(ctx, id)
	if err != nil {
		return ProposalDetail{}, err
	}
	detail := ProposalDetail{Proposal: p, Comments: comments}
	active, err := s.repo.ActiveRelease(ctx, p.Stroke)
	if err != nil {
		detail.Problems = "Este golpe todavía no tiene una base de conocimiento publicada."
		return detail, nil
	}
	detail.ActiveRelease = &active
	detail.Stale = p.BaseReleaseID != nil && *p.BaseReleaseID != active.ID && p.Status == domain.ProposalSubmitted
	if problem := s.checkAgainst(ctx, active, p.Changes); problem != nil {
		detail.Problems = problem.Error()
	}
	impacts, err := s.impacts(ctx, active, p.Changes)
	if err != nil {
		return ProposalDetail{}, err
	}
	detail.Impacts = impacts
	return detail, nil
}

// Preview calcula el impacto de una regla en edición, antes incluso de guardar la propuesta.
func (s *KnowledgeService) Preview(ctx context.Context, stroke domain.StrokeType, rule domain.Rule) (RuleImpact, int, error) {
	rule = rule.Normalize()
	if err := rule.Validate(); err != nil {
		return RuleImpact{}, 0, err
	}
	active, err := s.repo.ActiveRelease(ctx, stroke)
	if err != nil {
		return RuleImpact{}, 0, err
	}
	samples, err := s.repo.RecentSamples(ctx, stroke, s.cfg.PreviewSampleSize)
	if err != nil {
		return RuleImpact{}, 0, err
	}
	return s.impactOf(active, domain.Change{Op: domain.OpUpsertRule, Rule: &rule}, samples), len(samples), nil
}

func (s *KnowledgeService) impacts(ctx context.Context, active domain.Release, changes []domain.Change) ([]RuleImpact, error) {
	samples, err := s.repo.RecentSamples(ctx, active.Stroke, s.cfg.PreviewSampleSize)
	if err != nil {
		return nil, err
	}
	out := make([]RuleImpact, 0, len(changes))
	for _, c := range changes {
		out = append(out, s.impactOf(active, c, samples))
	}
	return out, nil
}

func (s *KnowledgeService) impactOf(active domain.Release, c domain.Change, samples []domain.MetricSample) RuleImpact {
	impact := RuleImpact{RuleID: c.TargetID()}
	if i := slices.IndexFunc(active.KB.Rules, func(r domain.Rule) bool { return r.ID == c.TargetID() }); i >= 0 {
		before := domain.CountRule(active.KB.Rules[i], samples, s.cfg.MinMetricConfidence)
		impact.Before = &before
	}
	if c.Op == domain.OpUpsertRule && c.Rule != nil {
		after := domain.CountRule(c.Rule.Normalize(), samples, s.cfg.MinMetricConfidence)
		impact.After = &after
	}
	return impact
}

// --- Acciones del experto ---

func (s *KnowledgeService) CreateProposal(ctx context.Context, author domain.User, in domain.ProposalInput) (domain.Proposal, error) {
	in = in.Sanitized()
	if err := in.Validate(); err != nil {
		return domain.Proposal{}, err
	}
	p, err := s.repo.CreateProposal(ctx, domain.Proposal{AuthorID: author.ID, ProposalInput: in, Status: domain.ProposalDraft})
	if err != nil {
		return domain.Proposal{}, err
	}
	s.audit(ctx, author.ID, "proposal.created", p.ID, map[string]any{"stroke": in.Stroke, "title": in.Title})
	return p, nil
}

func (s *KnowledgeService) UpdateProposal(ctx context.Context, author domain.User, id string, in domain.ProposalInput) (domain.Proposal, error) {
	p, err := s.ownEditable(ctx, author, id)
	if err != nil {
		return domain.Proposal{}, err
	}
	in = in.Sanitized()
	if err := in.Validate(); err != nil {
		return domain.Proposal{}, err
	}
	if in.Stroke != p.Stroke {
		return domain.Proposal{}, fmt.Errorf("%w: no se puede cambiar el golpe de una propuesta", domain.ErrInvalidInput)
	}
	p.ProposalInput = in
	if err := s.save(ctx, p, domain.ProposalDraft, domain.ProposalChangesRequested); err != nil {
		return domain.Proposal{}, err
	}
	return s.getProposal(ctx, id)
}

// Submit envía la propuesta a validación, anclada a la versión activa actual (base del diff y del
// control de conflictos). Reenviar tras "pedir cambios" vuelve a anclarla a la versión activa.
func (s *KnowledgeService) Submit(ctx context.Context, author domain.User, id string) (domain.Proposal, error) {
	p, err := s.ownEditable(ctx, author, id)
	if err != nil {
		return domain.Proposal{}, err
	}
	active, err := s.repo.ActiveRelease(ctx, p.Stroke)
	if err != nil {
		return domain.Proposal{}, fmt.Errorf("%w: este golpe todavía no tiene base de conocimiento publicada", domain.ErrInvalidInput)
	}
	if err := s.checkAgainst(ctx, active, p.Changes); err != nil {
		return domain.Proposal{}, err
	}
	from := p.Status
	p.Status, p.BaseReleaseID = domain.ProposalSubmitted, &active.ID
	if err := s.save(ctx, p, from); err != nil {
		return domain.Proposal{}, err
	}
	s.audit(ctx, author.ID, "proposal.submitted", p.ID, map[string]any{"base_release_id": active.ID})
	return s.getProposal(ctx, id)
}

func (s *KnowledgeService) Withdraw(ctx context.Context, author domain.User, id string) (domain.Proposal, error) {
	p, err := s.getProposal(ctx, id)
	if err != nil {
		return domain.Proposal{}, err
	}
	if p.AuthorID != author.ID {
		return domain.Proposal{}, fmt.Errorf("%w: solo el autor puede retirar su propuesta", domain.ErrForbidden)
	}
	from := []domain.ProposalStatus{domain.ProposalDraft, domain.ProposalSubmitted, domain.ProposalChangesRequested}
	if !slices.Contains(from, p.Status) {
		return domain.Proposal{}, fmt.Errorf("%w: la propuesta ya está %s", domain.ErrConflict, p.Status)
	}
	p.Status = domain.ProposalWithdrawn
	if err := s.save(ctx, p, from...); err != nil {
		return domain.Proposal{}, err
	}
	s.audit(ctx, author.ID, "proposal.withdrawn", p.ID, nil)
	return s.getProposal(ctx, id)
}

func (s *KnowledgeService) CreateMetricRequest(ctx context.Context, author domain.User, m domain.MetricRequest) (domain.MetricRequest, error) {
	m.AuthorID, m.Name, m.Description = author.ID, strings.TrimSpace(m.Name), strings.TrimSpace(m.Description)
	if err := m.Validate(); err != nil {
		return domain.MetricRequest{}, err
	}
	return s.repo.CreateMetricRequest(ctx, m)
}

func (s *KnowledgeService) MetricRequests(ctx context.Context) ([]domain.MetricRequest, error) {
	return s.repo.MetricRequests(ctx, knowledgeListLimit)
}

func (s *KnowledgeService) AddComment(ctx context.Context, author domain.User, proposalID, body string) (domain.Comment, error) {
	body = strings.TrimSpace(body)
	if body == "" || len(body) > 4000 {
		return domain.Comment{}, fmt.Errorf("%w: el comentario no puede estar vacío (máx. 4000)", domain.ErrInvalidInput)
	}
	if _, err := s.getProposal(ctx, proposalID); err != nil {
		return domain.Comment{}, err
	}
	return s.repo.AddComment(ctx, domain.Comment{ProposalID: proposalID, AuthorID: author.ID, Body: body})
}

// --- Acciones del validador ---

// Approve aplica la propuesta y publica una versión nueva en una sola transacción. Nadie aprueba lo suyo.
func (s *KnowledgeService) Approve(ctx context.Context, validator domain.User, id, note string) (domain.Release, error) {
	p, err := s.decidable(ctx, validator, id)
	if err != nil {
		return domain.Release{}, err
	}
	return s.repo.Approve(ctx, p.ID, validator.ID, strings.TrimSpace(note), buildRelease)
}

func buildRelease(active domain.Release, p domain.Proposal, catalog []domain.MetricInfo) (domain.KnowledgeBase, string, error) {
	if p.BaseReleaseID == nil || *p.BaseReleaseID != active.ID {
		return domain.KnowledgeBase{}, "", fmt.Errorf(
			"%w: la base de conocimiento cambió desde que se envió la propuesta; pide al experto que la revise y la reenvíe",
			domain.ErrConflict)
	}
	kb, err := domain.ApplyChanges(active.KB, p.Changes, true)
	if err != nil {
		return domain.KnowledgeBase{}, "", err
	}
	if err := kb.Validate(catalog); err != nil {
		return domain.KnowledgeBase{}, "", err
	}
	if kb.Version, err = domain.NextVersion(active.Version); err != nil {
		return domain.KnowledgeBase{}, "", err
	}
	ids := make([]string, 0, len(p.Changes))
	for _, c := range p.Changes {
		ids = append(ids, string(c.Op)+" "+c.TargetID())
	}
	return kb, fmt.Sprintf("%s (%s)", p.Title, strings.Join(ids, ", ")), nil
}

func (s *KnowledgeService) Reject(ctx context.Context, validator domain.User, id, note string) (domain.Proposal, error) {
	return s.decide(ctx, validator, id, domain.ProposalRejected, note)
}

func (s *KnowledgeService) RequestChanges(ctx context.Context, validator domain.User, id, note string) (domain.Proposal, error) {
	return s.decide(ctx, validator, id, domain.ProposalChangesRequested, note)
}

func (s *KnowledgeService) decide(ctx context.Context, validator domain.User, id string, to domain.ProposalStatus, note string) (domain.Proposal, error) {
	note = strings.TrimSpace(note)
	if note == "" || len(note) > 4000 {
		return domain.Proposal{}, fmt.Errorf("%w: explica el motivo al experto (nota obligatoria, máx. 4000)", domain.ErrInvalidInput)
	}
	if _, err := s.decidable(ctx, validator, id); err != nil {
		return domain.Proposal{}, err
	}
	ok, err := s.repo.Decide(ctx, id, validator.ID, to, note)
	if err != nil {
		return domain.Proposal{}, err
	}
	if !ok {
		return domain.Proposal{}, fmt.Errorf("%w: otra persona decidió esta propuesta entretanto", domain.ErrConflict)
	}
	s.audit(ctx, validator.ID, "proposal."+string(to), id, map[string]any{"note": note})
	return s.getProposal(ctx, id)
}

// Activate reactiva una versión anterior (rollback). Lo pueden hacer validadores y admins.
func (s *KnowledgeService) Activate(ctx context.Context, actor domain.User, releaseID string) (domain.Release, error) {
	if !domain.IsValidID(releaseID) {
		return domain.Release{}, domain.ErrResourceNotFound
	}
	return s.repo.Activate(ctx, releaseID, actor.ID)
}

// --- auxiliares ---

func (s *KnowledgeService) getProposal(ctx context.Context, id string) (domain.Proposal, error) {
	if !domain.IsValidID(id) {
		return domain.Proposal{}, domain.ErrResourceNotFound
	}
	return s.repo.Proposal(ctx, id)
}

func (s *KnowledgeService) ownEditable(ctx context.Context, author domain.User, id string) (domain.Proposal, error) {
	p, err := s.getProposal(ctx, id)
	if err != nil {
		return domain.Proposal{}, err
	}
	if p.AuthorID != author.ID {
		return domain.Proposal{}, fmt.Errorf("%w: solo el autor puede editar su propuesta", domain.ErrForbidden)
	}
	if !p.Editable() {
		return domain.Proposal{}, fmt.Errorf("%w: la propuesta está %s y no se puede editar", domain.ErrConflict, p.Status)
	}
	return p, nil
}

func (s *KnowledgeService) decidable(ctx context.Context, validator domain.User, id string) (domain.Proposal, error) {
	p, err := s.getProposal(ctx, id)
	if err != nil {
		return domain.Proposal{}, err
	}
	if p.AuthorID == validator.ID {
		return domain.Proposal{}, fmt.Errorf("%w: nadie valida sus propias propuestas", domain.ErrForbidden)
	}
	if p.Status != domain.ProposalSubmitted {
		return domain.Proposal{}, fmt.Errorf("%w: la propuesta está %s, no pendiente de validación", domain.ErrConflict, p.Status)
	}
	return p, nil
}

// checkAgainst valida que los cambios se puedan aplicar sobre la versión activa sin romperla.
func (s *KnowledgeService) checkAgainst(ctx context.Context, active domain.Release, changes []domain.Change) error {
	catalog, err := s.repo.Catalog(ctx, active.Stroke)
	if err != nil {
		return err
	}
	kb, err := domain.ApplyChanges(active.KB, changes, false)
	if err != nil {
		return err
	}
	return kb.Validate(catalog)
}

func (s *KnowledgeService) save(ctx context.Context, p domain.Proposal, from ...domain.ProposalStatus) error {
	p.UpdatedAt = s.now()
	ok, err := s.repo.UpdateProposal(ctx, p, from...)
	if err != nil {
		return err
	}
	if !ok {
		return fmt.Errorf("%w: la propuesta cambió de estado entretanto; recárgala", domain.ErrConflict)
	}
	return nil
}

// audit no debe tumbar una acción ya hecha: si falla, la acción queda y el error se descarta aquí
// (las acciones críticas —aprobar, rollback— auditan dentro de su propia transacción).
func (s *KnowledgeService) audit(ctx context.Context, actorID, action, proposalID string, details any) {
	_ = s.repo.Record(ctx, &actorID, action, "proposal:"+proposalID, details)
}
