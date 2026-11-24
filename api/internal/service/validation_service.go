package service

import (
	"context"
	"encoding/json"
	"fmt"
	"slices"
	"strings"
	"time"

	"tennisvision/api/internal/domain"
)

// Validación formal del conocimiento: corpus de referencia, revisiones por regla (libres y en campañas
// Delphi ciegas), ground truth de consenso, calibración de umbrales y KPIs de calidad de la base.
// Todo es control de calidad de las reglas: ninguna revisión cambia el análisis de un usuario.

// ReviewFilter selecciona revisiones. Vacío = todas.
type ReviewFilter struct {
	AnalysisID string
	CampaignID string
	Round      int
	ReviewerID string
	FreeOnly   bool              // solo revisiones libres (fuera de campañas)
	Stroke     domain.StrokeType // golpe del análisis revisado
}

// Professional es un experto o validador activo (posible evaluador de una campaña).
type Professional struct {
	ID    string        `json:"id"`
	Name  string        `json:"name"`
	Roles []domain.Role `json:"roles"`
}

type ValidationRepository interface {
	AddToCorpus(ctx context.Context, e domain.CorpusEntry) (domain.CorpusEntry, error)
	RemoveFromCorpus(ctx context.Context, analysisID string) (bool, error)
	// Corpus lista los casos del corpus (solo los que el dueño sigue permitiendo revisar).
	Corpus(ctx context.Context, stroke domain.StrokeType) ([]domain.CorpusEntry, error)

	// SaveReview crea o reemplaza (con sus ítems) la revisión de ese experto, caso, campaña y ronda.
	SaveReview(ctx context.Context, r domain.CaseReview) (domain.CaseReview, error)
	Reviews(ctx context.Context, f ReviewFilter) ([]domain.CaseReview, error)

	CreateCampaign(ctx context.Context, c domain.Campaign) (domain.Campaign, error)
	Campaign(ctx context.Context, id string) (domain.Campaign, error)
	Campaigns(ctx context.Context, stroke domain.StrokeType, limit int) ([]domain.Campaign, error)
	// AdvanceRound pasa de la ronda `from` a la siguiente si la campaña sigue abierta en `from`.
	AdvanceRound(ctx context.Context, id string, from int, actorID string) (bool, error)
	// CloseCampaign congela resultados y ground truth en una transacción (solo si sigue abierta en `round`).
	CloseCampaign(ctx context.Context, id string, round int, results domain.CampaignResults, gt []domain.GroundTruth, actorID string) (bool, error)

	// LabeledSamples: casos del corpus con ground truth de consenso (presente/ausente) y sus métricas.
	LabeledSamples(ctx context.Context, stroke domain.StrokeType, split string) ([]domain.LabeledSample, error)
	Professionals(ctx context.Context) ([]Professional, error)

	Record(ctx context.Context, actorID *string, action, target string, details any) error
}

// KnowledgeReader es la parte de la base de conocimiento que necesita la validación.
type KnowledgeReader interface {
	ActiveRelease(ctx context.Context, stroke domain.StrokeType) (domain.Release, error)
	RecentSamples(ctx context.Context, stroke domain.StrokeType, limit int) ([]domain.Sample, error)
}

type ValidationConfig struct {
	MinMetricConfidence float64
	SampleSize          int           // análisis recientes para la tasa de «medible»
	StaleAfter          time.Duration // una regla sin revisiones en este plazo pide auditoría
}

type ValidationService struct {
	repo   ValidationRepository
	cases  CaseRepository
	kb     KnowledgeReader
	signer MediaSigner
	ttl    time.Duration
	cfg    ValidationConfig
	now    func() time.Time
}

func NewValidation(repo ValidationRepository, cases CaseRepository, kb KnowledgeReader, signer MediaSigner,
	ttl time.Duration, cfg ValidationConfig, now func() time.Time) *ValidationService {
	if cfg.SampleSize <= 0 {
		cfg.SampleSize = 500
	}
	if cfg.StaleAfter <= 0 {
		cfg.StaleAfter = 90 * 24 * time.Hour // auditoría trimestral
	}
	return &ValidationService{repo: repo, cases: cases, kb: kb, signer: signer, ttl: ttl, cfg: cfg, now: now}
}

const validationListLimit = 200

// --- Corpus ---

func (s *ValidationService) AddToCorpus(ctx context.Context, actor domain.User, analysisID, note string) (domain.CorpusEntry, error) {
	a, err := s.reviewableCase(ctx, analysisID)
	if err != nil {
		return domain.CorpusEntry{}, err
	}
	note = strings.TrimSpace(note)
	if len(note) > 1000 {
		return domain.CorpusEntry{}, fmt.Errorf("%w: note máx. 1000", domain.ErrInvalidInput)
	}
	stroke := domain.SummarizeReport(a).Stroke
	if stroke == domain.StrokeAuto {
		return domain.CorpusEntry{}, fmt.Errorf("%w: el caso no tiene golpe identificado", domain.ErrInvalidInput)
	}
	e, err := s.repo.AddToCorpus(ctx, domain.CorpusEntry{AnalysisID: a.ID, Stroke: stroke, Split: domain.SplitFor(a.ID),
		Note: note, AddedBy: actor.ID})
	if err != nil {
		return domain.CorpusEntry{}, err
	}
	s.audit(ctx, actor.ID, "corpus.added", "analysis:"+a.ID, map[string]any{"split": e.Split, "stroke": stroke})
	return e, nil
}

func (s *ValidationService) RemoveFromCorpus(ctx context.Context, actor domain.User, analysisID string) error {
	if !domain.IsValidID(analysisID) {
		return domain.ErrResourceNotFound
	}
	ok, err := s.repo.RemoveFromCorpus(ctx, analysisID)
	if err != nil {
		return err
	}
	if !ok {
		return domain.ErrResourceNotFound
	}
	s.audit(ctx, actor.ID, "corpus.removed", "analysis:"+analysisID, nil)
	return nil
}

func (s *ValidationService) Corpus(ctx context.Context, stroke domain.StrokeType) ([]domain.CorpusEntry, error) {
	return s.repo.Corpus(ctx, stroke)
}

func (s *ValidationService) Professionals(ctx context.Context) ([]Professional, error) {
	return s.repo.Professionals(ctx)
}

// --- Revisión libre de un caso (ve el diagnóstico del sistema y da veredicto) ---

func (s *ValidationService) SaveFreeReview(ctx context.Context, reviewer domain.User, caseID string, in domain.ReviewInput) (domain.CaseReview, error) {
	a, err := s.reviewableCase(ctx, caseID)
	if err != nil {
		return domain.CaseReview{}, err
	}
	if a.UserID != nil && *a.UserID == reviewer.ID {
		return domain.CaseReview{}, fmt.Errorf("%w: nadie revisa sus propios videos", domain.ErrForbidden)
	}
	if err := in.Validate(false); err != nil {
		return domain.CaseReview{}, err
	}
	summary := domain.SummarizeReport(a)
	if err := s.knownRules(ctx, summary.Stroke, in.Items); err != nil {
		return domain.CaseReview{}, err
	}
	return s.repo.SaveReview(ctx, s.newReview(a, reviewer, summary, in, nil, nil))
}

// RuleTally agrega (de forma anónima) las revisiones libres de una regla en un caso.
type RuleTally struct {
	RuleID       string                 `json:"rule_id"`
	SystemFired  bool                   `json:"system_fired"`
	Consensus    domain.Consensus       `json:"ratings"`
	NotEvaluable int                    `json:"not_evaluable"`
	Verdicts     map[domain.Verdict]int `json:"verdicts"`
}

type CaseReviews struct {
	Mine   *domain.CaseReview
	Others int // cuántos otros expertos lo revisaron
	Rules  []RuleTally
}

// CaseReviews devuelve la revisión propia y el agregado anónimo de las demás (solo revisiones libres).
func (s *ValidationService) CaseReviews(ctx context.Context, viewer domain.User, caseID string) (CaseReviews, error) {
	a, err := s.reviewableCase(ctx, caseID)
	if err != nil {
		return CaseReviews{}, err
	}
	reviews, err := s.repo.Reviews(ctx, ReviewFilter{AnalysisID: a.ID, FreeOnly: true})
	if err != nil {
		return CaseReviews{}, err
	}
	out := CaseReviews{}
	ratings, tallies := map[string][]int{}, map[string]*RuleTally{}
	summary := domain.SummarizeReport(a)
	for i, r := range reviews {
		if r.ReviewerID == viewer.ID {
			out.Mine = &reviews[i]
		} else {
			out.Others++
		}
		for _, it := range r.Items {
			t, ok := tallies[it.RuleID]
			if !ok {
				t = &RuleTally{RuleID: it.RuleID, SystemFired: summary.Fired[it.RuleID], Verdicts: map[domain.Verdict]int{}}
				tallies[it.RuleID] = t
			}
			if it.Rating == nil {
				t.NotEvaluable++
			} else {
				ratings[it.RuleID] = append(ratings[it.RuleID], *it.Rating)
			}
			if it.Verdict != nil {
				t.Verdicts[*it.Verdict]++
			}
		}
	}
	for _, id := range slices.Sorted(mapKeys(tallies)) {
		t := tallies[id]
		t.Consensus = domain.ConsensusOf(ratings[id], 0.7, 2)
		out.Rules = append(out.Rules, *t)
	}
	return out, nil
}

// --- Campañas Delphi ---

func (s *ValidationService) CreateCampaign(ctx context.Context, actor domain.User, in domain.CampaignInput) (domain.Campaign, error) {
	in = in.WithDefaults()
	if err := in.Validate(); err != nil {
		return domain.Campaign{}, err
	}
	active, err := s.kb.ActiveRelease(ctx, in.Stroke)
	if err != nil {
		return domain.Campaign{}, fmt.Errorf("%w: este golpe no tiene base de conocimiento publicada", domain.ErrInvalidInput)
	}
	ruleIDs, err := campaignRules(active.KB, in.RuleIDs)
	if err != nil {
		return domain.Campaign{}, err
	}
	if err := s.checkCampaignCases(ctx, in); err != nil {
		return domain.Campaign{}, err
	}
	if err := s.checkRaters(ctx, in.RaterIDs); err != nil {
		return domain.Campaign{}, err
	}
	c, err := s.repo.CreateCampaign(ctx, domain.Campaign{Stroke: in.Stroke, Title: in.Title, RuleIDs: ruleIDs,
		Status: domain.CampaignOpen, CurrentRound: 1, MaxRounds: in.MaxRounds, ConsensusPct: in.ConsensusPct,
		MinRaters: in.MinRaters, CreatedBy: actor.ID, CaseIDs: in.CaseIDs, RaterIDs: in.RaterIDs})
	if err != nil {
		return domain.Campaign{}, err
	}
	s.audit(ctx, actor.ID, "campaign.created", "campaign:"+c.ID,
		map[string]any{"stroke": c.Stroke, "cases": len(c.CaseIDs), "raters": len(c.RaterIDs), "rules": len(c.RuleIDs)})
	return c, nil
}

func campaignRules(kb domain.KnowledgeBase, requested []string) ([]string, error) {
	all := make([]string, 0, len(kb.Rules))
	for _, r := range kb.Rules {
		all = append(all, r.ID)
	}
	if len(requested) == 0 {
		return all, nil
	}
	for _, id := range requested {
		if !slices.Contains(all, id) {
			return nil, fmt.Errorf("%w: la regla %s no está en la versión activa", domain.ErrInvalidInput, id)
		}
	}
	return requested, nil
}

func (s *ValidationService) checkCampaignCases(ctx context.Context, in domain.CampaignInput) error {
	corpus, err := s.repo.Corpus(ctx, in.Stroke)
	if err != nil {
		return err
	}
	inCorpus := map[string]bool{}
	for _, e := range corpus {
		inCorpus[e.AnalysisID] = true
	}
	for _, id := range in.CaseIDs {
		if !inCorpus[id] {
			return fmt.Errorf("%w: el caso %s no está en el corpus de %s", domain.ErrInvalidInput, id, in.Stroke)
		}
	}
	return nil
}

func (s *ValidationService) checkRaters(ctx context.Context, raterIDs []string) error {
	pros, err := s.repo.Professionals(ctx)
	if err != nil {
		return err
	}
	known := map[string]bool{}
	for _, p := range pros {
		known[p.ID] = true
	}
	for _, id := range raterIDs {
		if !known[id] {
			return fmt.Errorf("%w: %s no es un experto ni validador activo", domain.ErrInvalidInput, id)
		}
	}
	return nil
}

func (s *ValidationService) Campaigns(ctx context.Context, stroke domain.StrokeType) ([]domain.Campaign, error) {
	return s.repo.Campaigns(ctx, stroke, validationListLimit)
}

// RaterProgress: cuántos casos calificó cada evaluador en la ronda actual. El nombre solo lo ve el
// coordinador; entre evaluadores la campaña es anónima (Delphi).
type RaterProgress struct {
	RaterID string `json:"rater_id,omitempty"`
	Name    string `json:"name,omitempty"`
	Done    int    `json:"done"`
	Total   int    `json:"total"`
	IsMe    bool   `json:"is_me"`
}

type RoundStats struct {
	Round int                    `json:"round"`
	Rules []domain.RuleAgreement `json:"rules"`
}

type CampaignDetail struct {
	Campaign domain.Campaign
	IsRater  bool
	Progress []RaterProgress
	Rounds   []RoundStats // rondas terminadas (y la actual si quien mira no es evaluador)
	MyDone   []string     // casos que ya califiqué en la ronda actual
}

func (s *ValidationService) CampaignDetail(ctx context.Context, viewer domain.User, id string) (CampaignDetail, error) {
	c, err := s.getCampaign(ctx, id)
	if err != nil {
		return CampaignDetail{}, err
	}
	reviews, err := s.repo.Reviews(ctx, ReviewFilter{CampaignID: c.ID})
	if err != nil {
		return CampaignDetail{}, err
	}
	d := CampaignDetail{Campaign: c, IsRater: c.IsRater(viewer.ID)}
	coordinator := !d.IsRater || viewer.ID == c.CreatedBy
	names := map[string]string{}
	if coordinator {
		pros, err := s.repo.Professionals(ctx)
		if err != nil {
			return CampaignDetail{}, err
		}
		for _, p := range pros {
			names[p.ID] = p.Name
		}
	}
	done := map[string]int{}
	for _, r := range reviews {
		if *r.Round == c.CurrentRound {
			done[r.ReviewerID]++
			if r.ReviewerID == viewer.ID {
				d.MyDone = append(d.MyDone, r.AnalysisID)
			}
		}
	}
	for _, rid := range c.RaterIDs {
		p := RaterProgress{Done: done[rid], Total: len(c.CaseIDs), IsMe: rid == viewer.ID}
		if coordinator {
			p.RaterID, p.Name = rid, names[rid]
		}
		if coordinator || p.IsMe {
			d.Progress = append(d.Progress, p)
		}
	}
	lastVisible := c.CurrentRound - 1
	if c.Status == domain.CampaignClosed || !d.IsRater {
		lastVisible = c.CurrentRound
	}
	for round := 1; round <= lastVisible; round++ {
		d.Rounds = append(d.Rounds, RoundStats{Round: round, Rules: roundAgreement(c, reviewsOfRound(reviews, round))})
	}
	return d, nil
}

func reviewsOfRound(reviews []domain.CaseReview, round int) []domain.CaseReview {
	var out []domain.CaseReview
	for _, r := range reviews {
		if r.Round != nil && *r.Round == round {
			out = append(out, r)
		}
	}
	return out
}

func roundAgreement(c domain.Campaign, reviews []domain.CaseReview) []domain.RuleAgreement {
	out := make([]domain.RuleAgreement, 0, len(c.RuleIDs))
	for _, id := range c.RuleIDs {
		ra, _ := domain.Agreement(id, reviews, c.ConsensusPct, c.MinRaters)
		out = append(out, ra)
	}
	return out
}

// RuleFeedback es lo que ve un evaluador de la ronda anterior (Delphi): la distribución anónima del
// grupo y su propia calificación, para revisarla si quiere.
type RuleFeedback struct {
	RuleID       string           `json:"rule_id"`
	Group        domain.Consensus `json:"group"`
	MyRating     *int             `json:"my_rating"`
	MyNotEval    bool             `json:"my_not_evaluable"`
	NotEvaluable int              `json:"not_evaluable"`
}

type CampaignRule struct {
	ID          string `json:"id"`
	Title       string `json:"title"`
	Polarity    string `json:"polarity"`
	Explanation string `json:"explanation"`
}

// BlindCase es un caso de campaña sin el diagnóstico del sistema (evita el sesgo de anclaje).
type BlindCase struct {
	Campaign domain.Campaign
	Analysis domain.Analysis
	Segment  json.RawMessage
	Media    domain.MediaLinks
	Rules    []CampaignRule
	Mine     *domain.CaseReview
	Feedback []RuleFeedback // vacío en la ronda 1
}

func (s *ValidationService) CampaignCase(ctx context.Context, rater domain.User, campaignID, caseID string) (BlindCase, error) {
	c, a, err := s.raterCase(ctx, rater, campaignID, caseID)
	if err != nil {
		return BlindCase{}, err
	}
	media, err := SignMedia(ctx, s.signer, a, s.ttl, s.now())
	if err != nil {
		return BlindCase{}, err
	}
	out := BlindCase{Campaign: c, Analysis: a, Segment: domain.SummarizeReport(a).Segment, Media: media}
	if active, err := s.kb.ActiveRelease(ctx, c.Stroke); err == nil {
		for _, r := range active.KB.Rules {
			if slices.Contains(c.RuleIDs, r.ID) {
				out.Rules = append(out.Rules, CampaignRule{ID: r.ID, Title: r.Title, Polarity: r.Polarity, Explanation: r.Explanation})
			}
		}
	}
	reviews, err := s.repo.Reviews(ctx, ReviewFilter{CampaignID: c.ID, AnalysisID: a.ID})
	if err != nil {
		return BlindCase{}, err
	}
	for i, r := range reviews {
		if r.ReviewerID == rater.ID && *r.Round == c.CurrentRound {
			out.Mine = &reviews[i]
		}
	}
	if c.CurrentRound > 1 {
		out.Feedback = feedbackFor(c, reviewsOfRound(reviews, c.CurrentRound-1), rater.ID)
	}
	return out, nil
}

func feedbackFor(c domain.Campaign, previous []domain.CaseReview, raterID string) []RuleFeedback {
	out := make([]RuleFeedback, 0, len(c.RuleIDs))
	for _, id := range c.RuleIDs {
		f := RuleFeedback{RuleID: id}
		var ratings []int
		for _, r := range previous {
			for _, it := range r.Items {
				if it.RuleID != id {
					continue
				}
				if r.ReviewerID == raterID {
					f.MyRating, f.MyNotEval = it.Rating, it.NotEvaluable
				}
				if it.Rating == nil {
					f.NotEvaluable++
				} else {
					ratings = append(ratings, *it.Rating)
				}
			}
		}
		f.Group = domain.ConsensusOf(ratings, c.ConsensusPct, c.MinRaters)
		out = append(out, f)
	}
	return out
}

func (s *ValidationService) SaveCampaignReview(ctx context.Context, rater domain.User, campaignID, caseID string, in domain.ReviewInput) (domain.CaseReview, error) {
	c, a, err := s.raterCase(ctx, rater, campaignID, caseID)
	if err != nil {
		return domain.CaseReview{}, err
	}
	if c.Status != domain.CampaignOpen {
		return domain.CaseReview{}, domain.ErrCampaignClosed
	}
	if err := in.Validate(true); err != nil {
		return domain.CaseReview{}, err
	}
	for _, it := range in.Items {
		if !slices.Contains(c.RuleIDs, it.RuleID) {
			return domain.CaseReview{}, fmt.Errorf("%w: la regla %s no forma parte de la campaña", domain.ErrInvalidInput, it.RuleID)
		}
	}
	round := c.CurrentRound
	return s.repo.SaveReview(ctx, s.newReview(a, rater, domain.SummarizeReport(a), in, &c.ID, &round))
}

// Advance abre la siguiente ronda. Sin force exige que todos los evaluadores hayan calificado todo.
func (s *ValidationService) Advance(ctx context.Context, actor domain.User, id string, force bool) (domain.Campaign, error) {
	c, err := s.openCampaign(ctx, id)
	if err != nil {
		return domain.Campaign{}, err
	}
	if c.CurrentRound >= c.MaxRounds {
		return domain.Campaign{}, fmt.Errorf("%w: es la última ronda (%d); cierra la campaña", domain.ErrConflict, c.MaxRounds)
	}
	if !force {
		if missing, err := s.missing(ctx, c); err != nil {
			return domain.Campaign{}, err
		} else if missing > 0 {
			return domain.Campaign{}, fmt.Errorf("%w: faltan %d calificaciones en la ronda %d (usa force para avanzar igual)",
				domain.ErrConflict, missing, c.CurrentRound)
		}
	}
	ok, err := s.repo.AdvanceRound(ctx, c.ID, c.CurrentRound, actor.ID)
	if err != nil {
		return domain.Campaign{}, err
	}
	if !ok {
		return domain.Campaign{}, fmt.Errorf("%w: la campaña cambió entretanto; recárgala", domain.ErrConflict)
	}
	return s.getCampaign(ctx, id)
}

func (s *ValidationService) missing(ctx context.Context, c domain.Campaign) (int, error) {
	reviews, err := s.repo.Reviews(ctx, ReviewFilter{CampaignID: c.ID, Round: c.CurrentRound})
	if err != nil {
		return 0, err
	}
	return len(c.CaseIDs)*len(c.RaterIDs) - len(reviews), nil
}

// Close congela el ground truth: para cada caso y regla, la mediana de la última ronda con su
// etiqueta y si hubo consenso según la definición fijada al crear la campaña.
func (s *ValidationService) Close(ctx context.Context, actor domain.User, id string) (domain.Campaign, error) {
	c, err := s.openCampaign(ctx, id)
	if err != nil {
		return domain.Campaign{}, err
	}
	active, err := s.kb.ActiveRelease(ctx, c.Stroke)
	if err != nil {
		return domain.Campaign{}, err
	}
	polarity := map[string]string{}
	for _, r := range active.KB.Rules {
		polarity[r.ID] = r.Polarity
	}
	reviews, err := s.repo.Reviews(ctx, ReviewFilter{CampaignID: c.ID, Round: c.CurrentRound})
	if err != nil {
		return domain.Campaign{}, err
	}
	results := domain.CampaignResults{Round: c.CurrentRound}
	var gt []domain.GroundTruth
	for _, ruleID := range c.RuleIDs {
		ra, consensus := domain.Agreement(ruleID, reviews, c.ConsensusPct, c.MinRaters)
		results.Rules = append(results.Rules, ra)
		pol, known := polarity[ruleID]
		if !known {
			continue // la regla se retiró durante la campaña: queda el α, no el ground truth
		}
		for _, caseID := range slices.Sorted(mapKeys(consensus)) {
			cons := consensus[caseID]
			if cons.N < 2 {
				continue
			}
			gt = append(gt, domain.GroundTruth{AnalysisID: caseID, RuleID: ruleID, CampaignID: c.ID, Median: cons.Median,
				Label: domain.LabelFor(cons.Median, pol), Consensus: cons.Reached, Raters: cons.N})
		}
	}
	ok, err := s.repo.CloseCampaign(ctx, c.ID, c.CurrentRound, results, gt, actor.ID)
	if err != nil {
		return domain.Campaign{}, err
	}
	if !ok {
		return domain.Campaign{}, fmt.Errorf("%w: la campaña cambió entretanto; recárgala", domain.ErrConflict)
	}
	return s.getCampaign(ctx, id)
}

// --- auxiliares ---

func (s *ValidationService) newReview(a domain.Analysis, reviewer domain.User, summary domain.ReportSummary,
	in domain.ReviewInput, campaignID *string, round *int) domain.CaseReview {
	items := make([]domain.ReviewItem, len(in.Items))
	for i, it := range in.Items {
		fired := summary.Fired[it.RuleID] // lo que dijo el sistema, congelado al revisar
		it.SystemFired, it.Comment = &fired, strings.TrimSpace(it.Comment)
		items[i] = it
	}
	r := domain.CaseReview{AnalysisID: a.ID, ReviewerID: reviewer.ID, CampaignID: campaignID, Round: round,
		PlayerLevel: in.PlayerLevel, Notes: strings.TrimSpace(in.Notes), Items: items}
	if a.KBVersion != nil {
		r.KBVersion = a.KBVersion
	} else if summary.KBVersion != "" {
		r.KBVersion = &summary.KBVersion
	}
	return r
}

// knownRules exige que cada regla calificada exista en la versión activa del golpe.
func (s *ValidationService) knownRules(ctx context.Context, stroke domain.StrokeType, items []domain.ReviewItem) error {
	active, err := s.kb.ActiveRelease(ctx, stroke)
	if err != nil {
		return fmt.Errorf("%w: el golpe del caso no tiene base de conocimiento publicada", domain.ErrInvalidInput)
	}
	for _, it := range items {
		if !slices.ContainsFunc(active.KB.Rules, func(r domain.Rule) bool { return r.ID == it.RuleID }) {
			return fmt.Errorf("%w: la regla %s no está en la versión activa de %s", domain.ErrInvalidInput, it.RuleID, stroke)
		}
	}
	return nil
}

func (s *ValidationService) reviewableCase(ctx context.Context, id string) (domain.Analysis, error) {
	if !domain.IsValidID(id) {
		return domain.Analysis{}, domain.ErrNotFound
	}
	return s.cases.Case(ctx, id)
}

func (s *ValidationService) getCampaign(ctx context.Context, id string) (domain.Campaign, error) {
	if !domain.IsValidID(id) {
		return domain.Campaign{}, domain.ErrResourceNotFound
	}
	return s.repo.Campaign(ctx, id)
}

func (s *ValidationService) openCampaign(ctx context.Context, id string) (domain.Campaign, error) {
	c, err := s.getCampaign(ctx, id)
	if err != nil {
		return domain.Campaign{}, err
	}
	if c.Status != domain.CampaignOpen {
		return domain.Campaign{}, domain.ErrCampaignClosed
	}
	return c, nil
}

func (s *ValidationService) raterCase(ctx context.Context, rater domain.User, campaignID, caseID string) (domain.Campaign, domain.Analysis, error) {
	c, err := s.getCampaign(ctx, campaignID)
	if err != nil {
		return domain.Campaign{}, domain.Analysis{}, err
	}
	if !c.IsRater(rater.ID) {
		return domain.Campaign{}, domain.Analysis{}, domain.ErrNotARater
	}
	if !slices.Contains(c.CaseIDs, caseID) {
		return domain.Campaign{}, domain.Analysis{}, domain.ErrResourceNotFound
	}
	a, err := s.reviewableCase(ctx, caseID)
	if err != nil {
		return domain.Campaign{}, domain.Analysis{}, err
	}
	if a.UserID != nil && *a.UserID == rater.ID {
		return domain.Campaign{}, domain.Analysis{}, fmt.Errorf("%w: nadie califica sus propios videos", domain.ErrForbidden)
	}
	return c, a, nil
}

func (s *ValidationService) audit(ctx context.Context, actorID, action, target string, details any) {
	_ = s.repo.Record(ctx, &actorID, action, target, details)
}

func mapKeys[V any](m map[string]V) func(func(string) bool) {
	return func(yield func(string) bool) {
		for k := range m {
			if !yield(k) {
				return
			}
		}
	}
}
