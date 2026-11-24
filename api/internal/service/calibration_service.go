package service

import (
	"context"
	"fmt"
	"slices"
	"time"

	"tennisvision/api/internal/domain"
)

// --- Calibración: cómo acierta una regla candidata contra el ground truth de consenso ---

// Sweep barre el umbral de una condición (no `between`) para ver la curva precisión/recall.
type Sweep struct {
	Condition int     `json:"condition"` // índice dentro de when.all / when.any
	From      float64 `json:"from"`
	To        float64 `json:"to"`
	Steps     int     `json:"steps"`
}

type CalibrationRequest struct {
	Stroke domain.StrokeType `json:"stroke"`
	Rule   domain.Rule       `json:"rule"`
	Split  string            `json:"split"` // calibration (por defecto) | test
	Sweep  *Sweep            `json:"sweep"`
}

type Score struct {
	Confusion domain.Confusion `json:"confusion"`
	Precision *domain.Rate     `json:"precision"`
	Recall    *domain.Rate     `json:"recall"`
	F1        *float64         `json:"f1"`
}

func scoreOf(c domain.Confusion) Score {
	return Score{Confusion: c, Precision: c.Precision(), Recall: c.Recall(), F1: c.F1()}
}

type SweepPoint struct {
	Threshold float64 `json:"threshold"`
	Score
}

type CalibrationResult struct {
	Split        string `json:"split"`
	Labeled      int    `json:"labeled"`      // casos con ground truth para esta regla
	Unmeasurable int    `json:"unmeasurable"` // de esos, sin métricas fiables o fuera de vista
	Score
	Curve []SweepPoint `json:"curve,omitempty"`
}

const maxSweepSteps = 50

func (s *ValidationService) Calibrate(ctx context.Context, req CalibrationRequest) (CalibrationResult, error) {
	rule := req.Rule.Normalize()
	if err := rule.Validate(); err != nil {
		return CalibrationResult{}, err
	}
	if req.Split == "" {
		req.Split = domain.SplitCalibration
	}
	if !domain.ValidSplit(req.Split) {
		return CalibrationResult{}, fmt.Errorf("%w: split debe ser calibration o test", domain.ErrInvalidInput)
	}
	samples, err := s.labeledFor(ctx, req.Stroke, req.Split, rule.ID)
	if err != nil {
		return CalibrationResult{}, err
	}
	conf, unmeasurable := s.evaluate(rule, samples)
	out := CalibrationResult{Split: req.Split, Labeled: len(samples), Unmeasurable: unmeasurable, Score: scoreOf(conf)}
	if req.Sweep != nil {
		if out.Curve, err = s.sweep(rule, *req.Sweep, samples); err != nil {
			return CalibrationResult{}, err
		}
	}
	return out, nil
}

func (s *ValidationService) sweep(rule domain.Rule, sw Sweep, samples []domain.LabeledSample) ([]SweepPoint, error) {
	conds := rule.When.Conditions()
	switch {
	case sw.Condition < 0 || sw.Condition >= len(conds):
		return nil, fmt.Errorf("%w: sweep.condition fuera de rango", domain.ErrInvalidInput)
	case conds[sw.Condition].Op == "between":
		return nil, fmt.Errorf("%w: el barrido no admite between; ajusta min y max a mano", domain.ErrInvalidInput)
	case sw.Steps < 2 || sw.Steps > maxSweepSteps || sw.From >= sw.To:
		return nil, fmt.Errorf("%w: sweep necesita from < to y entre 2 y %d pasos", domain.ErrInvalidInput, maxSweepSteps)
	}
	curve := make([]SweepPoint, 0, sw.Steps)
	for i := range sw.Steps {
		t := sw.From + (sw.To-sw.From)*float64(i)/float64(sw.Steps-1)
		candidate := withThreshold(rule, sw.Condition, t)
		conf, _ := s.evaluate(candidate, samples)
		curve = append(curve, SweepPoint{Threshold: t, Score: scoreOf(conf)})
	}
	return curve, nil
}

func withThreshold(r domain.Rule, idx int, t float64) domain.Rule {
	clone := func(cs []domain.Condition) []domain.Condition {
		out := slices.Clone(cs)
		if idx < len(out) {
			out[idx].Value = domain.Threshold{Value: t}
		}
		return out
	}
	if len(r.When.All) > 0 {
		r.When.All = clone(r.When.All)
	} else {
		r.When.Any = clone(r.When.Any)
	}
	return r
}

func (s *ValidationService) labeledFor(ctx context.Context, stroke domain.StrokeType, split, ruleID string) ([]domain.LabeledSample, error) {
	all, err := s.repo.LabeledSamples(ctx, stroke, split)
	if err != nil {
		return nil, err
	}
	out := all[:0:0]
	for _, ls := range all {
		if ls.RuleID == ruleID {
			out = append(out, ls)
		}
	}
	return out, nil
}

func (s *ValidationService) evaluate(rule domain.Rule, samples []domain.LabeledSample) (domain.Confusion, int) {
	var c domain.Confusion
	unmeasurable := 0
	for _, ls := range samples {
		measurable, fired := rule.EvaluateSample(ls.Sample, s.cfg.MinMetricConfidence)
		if !measurable {
			unmeasurable++
			continue
		}
		c.Add(ls.Present, fired)
	}
	return c, unmeasurable
}

// RuleEvidence (EvidenceSource): la regla candidata contra el ground truth de test, con el α de la
// última campaña cerrada que la cubrió.
func (s *ValidationService) RuleEvidence(ctx context.Context, stroke domain.StrokeType, rule domain.Rule) (domain.Evidence, error) {
	samples, err := s.labeledFor(ctx, stroke, domain.SplitTest, rule.ID)
	if err != nil {
		return domain.Evidence{}, err
	}
	conf, _ := s.evaluate(rule, samples)
	ev := domain.Evidence{Confusion: conf}
	agreements, err := s.latestAgreements(ctx, stroke)
	if err != nil {
		return domain.Evidence{}, err
	}
	if a, ok := agreements[rule.ID]; ok {
		ev.Alpha, ev.Campaign = a.Alpha, a.campaignID
	}
	return ev, nil
}

type agreementAt struct {
	domain.RuleAgreement
	campaignID string
}

// latestAgreements: por regla, el acuerdo de la última campaña cerrada que la incluyó.
func (s *ValidationService) latestAgreements(ctx context.Context, stroke domain.StrokeType) (map[string]agreementAt, error) {
	campaigns, err := s.repo.Campaigns(ctx, stroke, validationListLimit)
	if err != nil {
		return nil, err
	}
	slices.SortFunc(campaigns, func(a, b domain.Campaign) int { return closedAt(b).Compare(closedAt(a)) })
	out := map[string]agreementAt{}
	for _, c := range campaigns {
		if c.Status != domain.CampaignClosed || c.Results == nil {
			continue
		}
		for _, ra := range c.Results.Rules {
			if _, seen := out[ra.RuleID]; !seen {
				out[ra.RuleID] = agreementAt{RuleAgreement: ra, campaignID: c.ID}
			}
		}
	}
	return out, nil
}

func closedAt(c domain.Campaign) time.Time {
	if c.ClosedAt == nil {
		return time.Time{}
	}
	return *c.ClosedAt
}

// --- KPIs de calidad de la base de conocimiento ---

type ReviewTally struct {
	Correct        int        `json:"correct"`
	FalsePositive  int        `json:"false_positive"`
	Missed         int        `json:"missed"`
	Unsure         int        `json:"unsure"`
	LastReviewedAt *time.Time `json:"last_reviewed_at"`
}

type RuleQuality struct {
	RuleID           string       `json:"rule_id"`
	Title            string       `json:"title"`
	Polarity         string       `json:"polarity"`
	ValidationStatus string       `json:"validation_status"`
	EvidenceLevel    string       `json:"evidence_level,omitempty"`
	Alpha            *float64     `json:"alpha"`
	AlphaBand        string       `json:"alpha_band,omitempty"`
	Test             Score        `json:"test"` // contra el ground truth de consenso (conjunto de test)
	TestUnmeasurable int          `json:"test_unmeasurable"`
	MeasurableRate   *float64     `json:"measurable_rate"` // en los análisis recientes
	FiredRate        *float64     `json:"fired_rate"`      // de los medibles
	Reviews          ReviewTally  `json:"reviews"`         // veredictos de las revisiones libres
	ReviewAccuracy   *domain.Rate `json:"review_accuracy"` // correct / (correct + fp + missed)
	NeedsAudit       []string     `json:"needs_audit"`
}

type CorpusStats struct {
	Total       int `json:"total"`
	Calibration int `json:"calibration"`
	Test        int `json:"test"`
}

type QualityReport struct {
	Stroke            domain.StrokeType `json:"stroke"`
	Version           string            `json:"version"`
	Rules             []RuleQuality     `json:"rules"`
	ByStatus          map[string]int    `json:"by_status"`
	ValidatedPct      float64           `json:"validated_pct"` // % con validation_status ≠ heuristic_unvalidated
	EmpiricalPct      float64           `json:"empirical_pct"`
	MeanAlpha         *float64          `json:"mean_alpha"`
	Corpus            CorpusStats       `json:"corpus"`
	GroundTruthItems  int               `json:"ground_truth_items"` // pares caso × regla con consenso
	TestAccuracy      *domain.Rate      `json:"test_accuracy"`      // (TP+TN)/N sumando todas las reglas
	RulesNeedingAudit int               `json:"rules_needing_audit"`
	GeneratedAt       time.Time         `json:"generated_at"`
}

// Umbrales del panel de auditoría.
const (
	auditMinSample     = 10  // por debajo, una precisión baja no es concluyente
	auditMinMeasurable = 0.5 // regla que casi nunca se puede medir: ¿métrica ruidosa o vista equivocada?
	auditMinRecent     = 20
)

func (s *ValidationService) Quality(ctx context.Context, stroke domain.StrokeType) (QualityReport, error) {
	active, err := s.kb.ActiveRelease(ctx, stroke)
	if err != nil {
		return QualityReport{}, err
	}
	test, err := s.repo.LabeledSamples(ctx, stroke, domain.SplitTest)
	if err != nil {
		return QualityReport{}, err
	}
	all, err := s.repo.LabeledSamples(ctx, stroke, "")
	if err != nil {
		return QualityReport{}, err
	}
	recent, err := s.kb.RecentSamples(ctx, stroke, s.cfg.SampleSize)
	if err != nil {
		return QualityReport{}, err
	}
	reviews, err := s.repo.Reviews(ctx, ReviewFilter{Stroke: stroke}) // las de campaña cuentan como revisión, sin veredicto
	if err != nil {
		return QualityReport{}, err
	}
	agreements, err := s.latestAgreements(ctx, stroke)
	if err != nil {
		return QualityReport{}, err
	}
	corpus, err := s.repo.Corpus(ctx, stroke)
	if err != nil {
		return QualityReport{}, err
	}

	q := QualityReport{Stroke: stroke, Version: active.Version, ByStatus: map[string]int{}, GroundTruthItems: len(all),
		GeneratedAt: s.now()}
	for _, e := range corpus {
		q.Corpus.Total++
		if e.Split == domain.SplitTest {
			q.Corpus.Test++
		} else {
			q.Corpus.Calibration++
		}
	}
	tallies := tallyReviews(reviews)
	var total domain.Confusion
	var alphaSum float64
	alphaN := 0
	for _, r := range active.KB.Rules {
		rq := s.ruleQuality(r.Normalize(), test, recent, tallies[r.ID], agreements)
		total.TP += rq.Test.Confusion.TP
		total.FP += rq.Test.Confusion.FP
		total.FN += rq.Test.Confusion.FN
		total.TN += rq.Test.Confusion.TN
		if rq.Alpha != nil {
			alphaSum += *rq.Alpha
			alphaN++
		}
		q.ByStatus[rq.ValidationStatus]++
		if len(rq.NeedsAudit) > 0 {
			q.RulesNeedingAudit++
		}
		q.Rules = append(q.Rules, rq)
	}
	if n := len(q.Rules); n > 0 {
		q.ValidatedPct = 100 * float64(n-q.ByStatus[domain.ValidationHeuristic]) / float64(n)
		q.EmpiricalPct = 100 * float64(q.ByStatus[domain.ValidationEmpirical]) / float64(n)
	}
	if alphaN > 0 {
		mean := alphaSum / float64(alphaN)
		q.MeanAlpha = &mean
	}
	if n := total.N(); n > 0 {
		lo, hi := domain.Wilson(total.TP+total.TN, n)
		q.TestAccuracy = &domain.Rate{Value: float64(total.TP+total.TN) / float64(n), Low: lo, High: hi, N: n}
	}
	return q, nil
}

func (s *ValidationService) ruleQuality(r domain.Rule, test []domain.LabeledSample, recent []domain.Sample,
	tally ReviewTally, agreements map[string]agreementAt) RuleQuality {
	rq := RuleQuality{RuleID: r.ID, Title: r.Title, Polarity: r.Polarity, ValidationStatus: r.ValidationStatus,
		EvidenceLevel: r.EvidenceLevel, Reviews: tally, NeedsAudit: []string{}}
	var mine []domain.LabeledSample
	for _, ls := range test {
		if ls.RuleID == r.ID {
			mine = append(mine, ls)
		}
	}
	conf, unmeasurable := s.evaluate(r, mine)
	rq.Test, rq.TestUnmeasurable = scoreOf(conf), unmeasurable
	if a, ok := agreements[r.ID]; ok && a.Alpha != nil {
		rq.Alpha, rq.AlphaBand = a.Alpha, a.Band
	}
	if len(recent) > 0 {
		count := domain.CountRule(r, recent, s.cfg.MinMetricConfidence)
		m := float64(count.Measurable) / float64(len(recent))
		rq.MeasurableRate = &m
		if count.Measurable > 0 {
			f := float64(count.Fired) / float64(count.Measurable)
			rq.FiredRate = &f
		}
	}
	if decided := tally.Correct + tally.FalsePositive + tally.Missed; decided > 0 {
		lo, hi := domain.Wilson(tally.Correct, decided)
		rq.ReviewAccuracy = &domain.Rate{Value: float64(tally.Correct) / float64(decided), Low: lo, High: hi, N: decided}
	}
	rq.NeedsAudit = s.auditReasons(rq, len(recent))
	return rq
}

func (s *ValidationService) auditReasons(rq RuleQuality, recent int) []string {
	reasons := []string{}
	add := func(cond bool, msg string) {
		if cond {
			reasons = append(reasons, msg)
		}
	}
	last := rq.Reviews.LastReviewedAt
	add(last == nil, "nunca revisada sobre casos reales")
	add(last != nil && s.now().Sub(*last) > s.cfg.StaleAfter,
		fmt.Sprintf("sin revisiones en los últimos %d días", int(s.cfg.StaleAfter.Hours()/24)))
	p, r := rq.Test.Precision, rq.Test.Recall
	add(p != nil && p.N >= auditMinSample && p.Value < domain.EmpiricalMinPrecision, "precisión en test por debajo de 0,8")
	add(r != nil && r.N >= auditMinSample && r.Value < domain.EmpiricalMinRecall, "recall en test por debajo de 0,8")
	add(rq.Alpha != nil && *rq.Alpha < domain.AlphaTentative, "los expertos no se ponen de acuerdo sobre este aspecto (α < 0,667)")
	add(recent >= auditMinRecent && rq.MeasurableRate != nil && *rq.MeasurableRate < auditMinMeasurable,
		"se puede medir en menos de la mitad de los análisis")
	if rq.ValidationStatus == domain.ValidationEmpirical {
		ev := domain.Evidence{Confusion: rq.Test.Confusion, Alpha: rq.Alpha}
		add(ev.Sufficient() != nil, "marcada como validada empíricamente, pero la evidencia actual ya no alcanza")
	}
	return reasons
}

func tallyReviews(reviews []domain.CaseReview) map[string]ReviewTally {
	out := map[string]ReviewTally{}
	for _, rv := range reviews {
		for _, it := range rv.Items {
			t := out[it.RuleID]
			if it.Verdict != nil {
				switch *it.Verdict {
				case domain.VerdictCorrect:
					t.Correct++
				case domain.VerdictFalsePositive:
					t.FalsePositive++
				case domain.VerdictMissed:
					t.Missed++
				default:
					t.Unsure++
				}
			}
			if t.LastReviewedAt == nil || rv.SubmittedAt.After(*t.LastReviewedAt) {
				at := rv.SubmittedAt
				t.LastReviewedAt = &at
			}
			out[it.RuleID] = t
		}
	}
	return out
}
