package service

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"strings"
	"testing"
	"time"

	"tennisvision/api/internal/domain"
)

// fakeValidationRepo reproduce en memoria las garantías del repositorio real: una revisión por
// experto/caso/campaña/ronda, rondas que solo avanzan desde la actual y cierre único.
type fakeValidationRepo struct {
	corpus    map[string]domain.CorpusEntry
	reviews   []domain.CaseReview
	campaigns map[string]domain.Campaign
	gt        map[[2]string]domain.GroundTruth
	pros      []Professional
	cases     *fakeCases
	audits    []string
	seq       int
}

func (r *fakeValidationRepo) id() string {
	r.seq++
	return fmt.Sprintf("cccccccc-0000-4000-8000-%012d", r.seq)
}

func (r *fakeValidationRepo) AddToCorpus(_ context.Context, e domain.CorpusEntry) (domain.CorpusEntry, error) {
	if _, dup := r.corpus[e.AnalysisID]; dup {
		return domain.CorpusEntry{}, domain.ErrConflict
	}
	r.corpus[e.AnalysisID] = e
	return e, nil
}

func (r *fakeValidationRepo) RemoveFromCorpus(_ context.Context, id string) (bool, error) {
	_, ok := r.corpus[id]
	delete(r.corpus, id)
	return ok, nil
}

func (r *fakeValidationRepo) Corpus(_ context.Context, stroke domain.StrokeType) ([]domain.CorpusEntry, error) {
	var out []domain.CorpusEntry
	for _, e := range r.corpus {
		if stroke == "" || e.Stroke == stroke {
			out = append(out, e)
		}
	}
	return out, nil
}

func (r *fakeValidationRepo) SaveReview(_ context.Context, rv domain.CaseReview) (domain.CaseReview, error) {
	r.reviews = slices.DeleteFunc(r.reviews, func(o domain.CaseReview) bool {
		return o.AnalysisID == rv.AnalysisID && o.ReviewerID == rv.ReviewerID &&
			ptrEq(o.CampaignID, rv.CampaignID) && ptrEq(o.Round, rv.Round)
	})
	rv.ID, rv.SubmittedAt = r.id(), fixedNow
	r.reviews = append(r.reviews, rv)
	return rv, nil
}

func ptrEq[T comparable](a, b *T) bool {
	return (a == nil && b == nil) || (a != nil && b != nil && *a == *b)
}

func (r *fakeValidationRepo) Reviews(_ context.Context, f ReviewFilter) ([]domain.CaseReview, error) {
	var out []domain.CaseReview
	for _, rv := range r.reviews {
		switch {
		case f.AnalysisID != "" && rv.AnalysisID != f.AnalysisID,
			f.CampaignID != "" && (rv.CampaignID == nil || *rv.CampaignID != f.CampaignID),
			f.Round != 0 && (rv.Round == nil || *rv.Round != f.Round),
			f.ReviewerID != "" && rv.ReviewerID != f.ReviewerID,
			f.FreeOnly && rv.CampaignID != nil:
			continue
		}
		out = append(out, rv)
	}
	return out, nil
}

func (r *fakeValidationRepo) CreateCampaign(_ context.Context, c domain.Campaign) (domain.Campaign, error) {
	c.ID, c.CreatedAt = r.id(), fixedNow
	r.campaigns[c.ID] = c
	return c, nil
}

func (r *fakeValidationRepo) Campaign(_ context.Context, id string) (domain.Campaign, error) {
	c, ok := r.campaigns[id]
	if !ok {
		return domain.Campaign{}, domain.ErrResourceNotFound
	}
	return c, nil
}

func (r *fakeValidationRepo) Campaigns(context.Context, domain.StrokeType, int) ([]domain.Campaign, error) {
	var out []domain.Campaign
	for _, c := range r.campaigns {
		out = append(out, c)
	}
	return out, nil
}

func (r *fakeValidationRepo) AdvanceRound(_ context.Context, id string, from int, _ string) (bool, error) {
	c := r.campaigns[id]
	if c.Status != domain.CampaignOpen || c.CurrentRound != from || from >= c.MaxRounds {
		return false, nil
	}
	c.CurrentRound++
	r.campaigns[id] = c
	return true, nil
}

func (r *fakeValidationRepo) CloseCampaign(_ context.Context, id string, round int, res domain.CampaignResults,
	gt []domain.GroundTruth, _ string) (bool, error) {
	c := r.campaigns[id]
	if c.Status != domain.CampaignOpen || c.CurrentRound != round {
		return false, nil
	}
	now := fixedNow
	c.Status, c.Results, c.ClosedAt = domain.CampaignClosed, &res, &now
	r.campaigns[id] = c
	for _, g := range gt {
		r.gt[[2]string{g.AnalysisID, g.RuleID}] = g
	}
	return true, nil
}

func (r *fakeValidationRepo) LabeledSamples(_ context.Context, stroke domain.StrokeType, split string) ([]domain.LabeledSample, error) {
	var out []domain.LabeledSample
	for key, g := range r.gt {
		e, inCorpus := r.corpus[key[0]]
		if !inCorpus || e.Stroke != stroke || (split != "" && e.Split != split) || !g.Consensus || g.Label == domain.LabelIndeterminate {
			continue
		}
		a, _ := r.cases.Case(context.Background(), key[0])
		out = append(out, domain.LabeledSample{Sample: domain.Sample{ID: a.ID, View: a.Params.CameraView,
			Metrics: metricsOf(a)}, RuleID: g.RuleID, Present: g.Label == domain.LabelPresent})
	}
	return out, nil
}

func (r *fakeValidationRepo) Professionals(context.Context) ([]Professional, error) {
	return r.pros, nil
}

func (r *fakeValidationRepo) Record(_ context.Context, _ *string, action, _ string, _ any) error {
	r.audits = append(r.audits, action)
	return nil
}

// caseKnee: ángulo mínimo de rodilla de cada caso de prueba (las métricas que devolvería el corpus).
var caseKnee = map[string]float64{}

func metricsOf(a domain.Analysis) domain.MetricSample {
	return domain.MetricSample{"knee_angle_min": {Value: caseKnee[a.ID], Confidence: 0.9}}
}

var (
	owner = "dddddddd-0000-4000-8000-00000000000d"
	carla = domain.User{ID: "eeeeeeee-0000-4000-8000-00000000000e", Roles: []domain.Role{domain.RoleExpert}}
	dani  = domain.User{ID: "ffffffff-0000-4000-8000-00000000000f", Roles: []domain.Role{domain.RoleExpert}}
)

// reviewCase es un análisis de derecha completado en el que disparó FH-POST-001 si fired.
func reviewCase(i int, knee float64, fired bool) domain.Analysis {
	id := fmt.Sprintf("11111111-0000-4000-8000-%012d", i)
	caseKnee[id] = knee
	findings := `[]`
	if fired {
		findings = `[{"rule_id":"FH-POST-001"}]`
	}
	report := fmt.Sprintf(`{"stroke_type":"forehand","segment":{"impact_frame":10},"findings":%s,"strengths":[]}`, findings)
	return domain.Analysis{ID: id, UserID: &owner, Status: domain.StatusCompleted, VideoKey: "videos/" + id,
		Report: []byte(report), Params: domain.AnalysisParams{StrokeType: domain.StrokeForehand, CameraView: domain.ViewSide},
		CreatedAt: fixedNow}
}

type validationFixture struct {
	svc   *ValidationService
	repo  *fakeValidationRepo
	kb    *fakeKnowledgeRepo
	cases *fakeCases
}

func newValidationSvc(t *testing.T, cases ...domain.Analysis) validationFixture {
	t.Helper()
	fc := &fakeCases{items: cases}
	repo := &fakeValidationRepo{corpus: map[string]domain.CorpusEntry{}, campaigns: map[string]domain.Campaign{},
		gt: map[[2]string]domain.GroundTruth{}, cases: fc, pros: []Professional{
			{ID: ana.ID, Name: "Ana"}, {ID: vicky.ID, Name: "Vicky"}, {ID: carla.ID, Name: "Carla"}, {ID: dani.ID, Name: "Dani"}}}
	kb := newFakeKnowledgeRepo(t)
	svc := NewValidation(repo, fc, kb, &fakeStore{}, time.Hour, ValidationConfig{MinMetricConfidence: 0.5}, func() time.Time { return fixedNow })
	return validationFixture{svc: svc, repo: repo, kb: kb, cases: fc}
}

func rating(v int) *int { return &v }

func rate(rule string, v int) domain.ReviewInput {
	return domain.ReviewInput{Items: []domain.ReviewItem{{RuleID: rule, Rating: rating(v)}}}
}

func TestFreeReviewFreezesSystemOutputAndForbidsOwner(t *testing.T) {
	c := reviewCase(1, 170, true)
	f := newValidationSvc(t, c)
	ctx := context.Background()
	v := domain.VerdictFalsePositive
	in := domain.ReviewInput{Items: []domain.ReviewItem{{RuleID: "FH-POST-001", Rating: rating(6), Verdict: &v}}}

	if _, err := f.svc.SaveFreeReview(ctx, domain.User{ID: owner}, c.ID, in); !errors.Is(err, domain.ErrForbidden) {
		t.Fatalf("el dueño no revisa su video: %v", err)
	}
	r, err := f.svc.SaveFreeReview(ctx, carla, c.ID, in)
	if err != nil {
		t.Fatal(err)
	}
	if fired := r.Items[0].SystemFired; fired == nil || !*fired {
		t.Fatal("system_fired se congela desde el reporte")
	}
	// Reenviar reemplaza (una revisión libre por experto y caso).
	if _, err := f.svc.SaveFreeReview(ctx, carla, c.ID, rate("FH-POST-001", 5)); err != nil {
		t.Fatal(err)
	}
	if _, err := f.svc.SaveFreeReview(ctx, dani, c.ID, in); err != nil {
		t.Fatal(err)
	}
	if len(f.repo.reviews) != 2 {
		t.Fatalf("revisiones: %d", len(f.repo.reviews))
	}
	agg, err := f.svc.CaseReviews(ctx, carla, c.ID)
	if err != nil || agg.Mine == nil || agg.Others != 1 || len(agg.Rules) != 1 {
		t.Fatalf("agregado: %+v %v", agg, err)
	}
	if agg.Rules[0].Verdicts[domain.VerdictFalsePositive] != 1 || agg.Rules[0].Consensus.Median != 5.5 {
		t.Fatalf("tally: %+v", agg.Rules[0])
	}
	if _, err := f.svc.SaveFreeReview(ctx, carla, c.ID, rate("NOPE-001", 3)); !errors.Is(err, domain.ErrInvalidInput) {
		t.Fatalf("regla inexistente: %v", err)
	}
}

func TestCorpusSplitAndDuplicates(t *testing.T) {
	c := reviewCase(1, 170, true)
	f := newValidationSvc(t, c)
	e, err := f.svc.AddToCorpus(context.Background(), vicky, c.ID, "buen ejemplo")
	if err != nil || e.Split != domain.SplitFor(c.ID) || e.Stroke != domain.StrokeForehand {
		t.Fatalf("corpus: %+v %v", e, err)
	}
	if _, err := f.svc.AddToCorpus(context.Background(), vicky, c.ID, ""); !errors.Is(err, domain.ErrConflict) {
		t.Fatalf("duplicado: %v", err)
	}
	if _, err := f.svc.AddToCorpus(context.Background(), vicky, "11111111-0000-4000-8000-999999999999", ""); !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("caso no revisable: %v", err)
	}
}

// Delphi completo: ronda 1 con desacuerdo, feedback anónimo, ronda 2 con consenso y cierre.
func TestDelphiCampaignLifecycle(t *testing.T) {
	c1, c2 := reviewCase(1, 170, true), reviewCase(2, 150, false)
	f := newValidationSvc(t, c1, c2)
	ctx := context.Background()
	for _, c := range []domain.Analysis{c1, c2} {
		if _, err := f.svc.AddToCorpus(ctx, vicky, c.ID, ""); err != nil {
			t.Fatal(err)
		}
	}
	camp, err := f.svc.CreateCampaign(ctx, vicky, domain.CampaignInput{Stroke: domain.StrokeForehand, Title: "Piloto",
		RuleIDs: []string{"FH-POST-001"}, CaseIDs: []string{c1.ID, c2.ID}, RaterIDs: []string{ana.ID, carla.ID, dani.ID}, MaxRounds: 2})
	if err != nil {
		t.Fatal(err)
	}

	if _, err := f.svc.CampaignCase(ctx, vicky, camp.ID, c1.ID); !errors.Is(err, domain.ErrNotARater) {
		t.Fatalf("no evaluador: %v", err)
	}
	blind, err := f.svc.CampaignCase(ctx, carla, camp.ID, c1.ID)
	if err != nil || len(blind.Rules) != 1 || blind.Media.VideoURL == "" || len(blind.Feedback) != 0 {
		t.Fatalf("vista ciega: %+v %v", blind, err)
	}
	v := domain.VerdictCorrect
	withVerdict := domain.ReviewInput{Items: []domain.ReviewItem{{RuleID: "FH-POST-001", Rating: rating(2), Verdict: &v}}}
	if _, err := f.svc.SaveCampaignReview(ctx, carla, camp.ID, c1.ID, withVerdict); !errors.Is(err, domain.ErrInvalidInput) {
		t.Fatalf("en campaña ciega no hay veredicto: %v", err)
	}

	// Ronda 1: en el caso 1 hay desacuerdo (2, 2, 6); en el caso 2, acuerdo (6, 6, 7).
	round1 := map[string][2]int{ana.ID: {2, 6}, carla.ID: {2, 6}, dani.ID: {6, 7}}
	for _, u := range []domain.User{ana, carla} {
		for i, c := range []domain.Analysis{c1, c2} {
			if _, err := f.svc.SaveCampaignReview(ctx, u, camp.ID, c.ID, rate("FH-POST-001", round1[u.ID][i])); err != nil {
				t.Fatal(err)
			}
		}
	}
	if _, err := f.svc.Advance(ctx, vicky, camp.ID, false); !errors.Is(err, domain.ErrConflict) {
		t.Fatalf("faltan calificaciones: %v", err)
	}
	for i, c := range []domain.Analysis{c1, c2} {
		if _, err := f.svc.SaveCampaignReview(ctx, dani, camp.ID, c.ID, rate("FH-POST-001", round1[dani.ID][i])); err != nil {
			t.Fatal(err)
		}
	}
	// Un evaluador no ve las estadísticas de la ronda en curso; el coordinador sí.
	if d, _ := f.svc.CampaignDetail(ctx, carla, camp.ID); len(d.Rounds) != 0 || len(d.Progress) != 1 || d.Progress[0].RaterID != "" {
		t.Fatalf("anonimato del evaluador: %+v", d)
	}
	if d, _ := f.svc.CampaignDetail(ctx, vicky, camp.ID); len(d.Rounds) != 1 || len(d.Progress) != 3 || d.Progress[0].Name == "" {
		t.Fatalf("vista del coordinador: %+v", d)
	}
	if _, err := f.svc.Advance(ctx, vicky, camp.ID, false); err != nil {
		t.Fatal(err)
	}

	// Ronda 2: el feedback muestra la distribución anónima y mi calificación anterior.
	fb, err := f.svc.CampaignCase(ctx, dani, camp.ID, c1.ID)
	if err != nil || len(fb.Feedback) != 1 || fb.Feedback[0].Group.Median != 2 || *fb.Feedback[0].MyRating != 6 {
		t.Fatalf("feedback Delphi: %+v %v", fb.Feedback, err)
	}
	round2 := map[string][2]int{ana.ID: {2, 6}, carla.ID: {2, 6}, dani.ID: {3, 7}}
	for _, u := range []domain.User{ana, carla, dani} {
		for i, c := range []domain.Analysis{c1, c2} {
			if _, err := f.svc.SaveCampaignReview(ctx, u, camp.ID, c.ID, rate("FH-POST-001", round2[u.ID][i])); err != nil {
				t.Fatal(err)
			}
		}
	}
	if _, err := f.svc.Advance(ctx, vicky, camp.ID, true); !errors.Is(err, domain.ErrConflict) {
		t.Fatalf("no se avanza más allá de max_rounds: %v", err)
	}
	closed, err := f.svc.Close(ctx, vicky, camp.ID)
	if err != nil || closed.Status != domain.CampaignClosed || closed.Results == nil || closed.Results.Round != 2 {
		t.Fatalf("cierre: %+v %v", closed, err)
	}
	g1, g2 := f.repo.gt[[2]string{c1.ID, "FH-POST-001"}], f.repo.gt[[2]string{c2.ID, "FH-POST-001"}]
	if g1.Label != domain.LabelPresent || !g1.Consensus || g2.Label != domain.LabelAbsent || g2.Raters != 3 {
		t.Fatalf("ground truth: %+v %+v", g1, g2)
	}
	if _, err := f.svc.SaveCampaignReview(ctx, ana, camp.ID, c1.ID, rate("FH-POST-001", 4)); !errors.Is(err, domain.ErrCampaignClosed) {
		t.Fatalf("campaña cerrada: %v", err)
	}
}

// labeledCorpus crea n casos con ground truth para FH-POST-001: piernas rígidas (rodilla > 162°) = presente.
func labeledCorpus(t *testing.T, n int) validationFixture {
	t.Helper()
	var cases []domain.Analysis
	for i := range n {
		knee := 150 + float64(i%25) // 150..174
		cases = append(cases, reviewCase(100+i, knee, knee > 160))
	}
	f := newValidationSvc(t, cases...)
	camp := "cccccccc-0000-4000-8000-999999999999"
	f.repo.campaigns[camp] = domain.Campaign{ID: camp, Stroke: domain.StrokeForehand, Status: domain.CampaignClosed,
		Results: &domain.CampaignResults{Rules: []domain.RuleAgreement{{RuleID: "FH-POST-001", Alpha: ptr(0.85)}}}}
	for _, c := range cases {
		f.repo.corpus[c.ID] = domain.CorpusEntry{AnalysisID: c.ID, Stroke: domain.StrokeForehand, Split: domain.SplitFor(c.ID)}
		label := domain.LabelAbsent
		if caseKnee[c.ID] > 162 {
			label = domain.LabelPresent
		}
		f.repo.gt[[2]string{c.ID, "FH-POST-001"}] = domain.GroundTruth{AnalysisID: c.ID, RuleID: "FH-POST-001",
			CampaignID: camp, Label: label, Consensus: true, Raters: 3}
	}
	return f
}

func ptr[T any](v T) *T { return &v }

func TestCalibrationSweepFindsTheExpertThreshold(t *testing.T) {
	f := labeledCorpus(t, 200)
	rule := kneeProposal().Changes[0].Rule
	res, err := f.svc.Calibrate(context.Background(), CalibrationRequest{Stroke: domain.StrokeForehand, Rule: *rule,
		Sweep: &Sweep{Condition: 0, From: 155, To: 170, Steps: 16}})
	if err != nil || res.Split != domain.SplitCalibration || res.Labeled == 0 || len(res.Curve) != 16 {
		t.Fatalf("calibración: %+v %v", res, err)
	}
	best := slices.MaxFunc(res.Curve, func(a, b SweepPoint) int {
		fa, fb := 0.0, 0.0
		if a.F1 != nil {
			fa = *a.F1
		}
		if b.F1 != nil {
			fb = *b.F1
		}
		return map[bool]int{true: 1, false: -1}[fa > fb]
	})
	if best.Threshold != 162 || *best.F1 != 1 {
		t.Fatalf("el mejor umbral debe ser 162 con F1=1: %+v", best)
	}
	between := *rule
	between.When.All = []domain.Condition{{Metric: "knee_angle_min", Op: "between", Value: domain.Threshold{Low: 1, High: 2, IsRange: true}}}
	if _, err := f.svc.Calibrate(context.Background(), CalibrationRequest{Stroke: domain.StrokeForehand, Rule: between,
		Sweep: &Sweep{From: 1, To: 2, Steps: 3}}); !errors.Is(err, domain.ErrInvalidInput) {
		t.Fatalf("between no se barre: %v", err)
	}
}

func TestEmpiricalValidationRequiresEvidenceOnApproval(t *testing.T) {
	f := labeledCorpus(t, 200)
	knowledge := NewKnowledge(f.kb, KnowledgeConfig{MinMetricConfidence: 0.5}, time.Now).WithEvidence(f.svc)
	ctx := context.Background()

	propose := func(threshold float64) domain.Proposal {
		in := kneeProposal()
		in.Changes[0].Rule.When.All[0].Value.Value = threshold
		in.Changes[0].Rule.ValidationStatus = domain.ValidationEmpirical
		p, err := knowledge.CreateProposal(ctx, ana, in)
		if err != nil {
			t.Fatal(err)
		}
		if p, err = knowledge.Submit(ctx, ana, p.ID); err != nil {
			t.Fatal(err)
		}
		return p
	}
	// Con 150° dispara casi siempre: precisión baja → no puede ser empirically_validated.
	if _, err := knowledge.Approve(ctx, vicky, propose(150).ID, ""); !errors.Is(err, domain.ErrEvidenceInsufficient) {
		t.Fatalf("evidencia insuficiente: %v", err)
	}
	rel, err := knowledge.Approve(ctx, vicky, propose(162).ID, "")
	if err != nil {
		t.Fatal(err)
	}
	var got domain.Rule
	for _, r := range rel.KB.Rules {
		if r.ID == "FH-POST-001" {
			got = r
		}
	}
	v := got.Validation
	if got.ValidationStatus != domain.ValidationEmpirical || v == nil || v.N < domain.EmpiricalMinN ||
		*v.Precision != 1 || *v.Alpha != 0.85 || v.ValidatedBy[0] != vicky.ID {
		t.Fatalf("traza de validación: %+v %+v", got, v)
	}
	if rel.Bump == nil || *rel.Bump != domain.BumpMinor || len(rel.Entries) != 1 || rel.Entries[0].Thresholds[0].After != "gt 162" {
		t.Fatalf("changelog: %+v %v", rel.Entries, rel.Bump)
	}
}

func TestQualityFlagsRulesForAudit(t *testing.T) {
	f := labeledCorpus(t, 200)
	f.kb.samples = []domain.Sample{{View: domain.ViewSide, Metrics: domain.MetricSample{"knee_angle_min": {Value: 170, Confidence: 0.9}}}}
	q, err := f.svc.Quality(context.Background(), domain.StrokeForehand)
	if err != nil {
		t.Fatal(err)
	}
	if q.Corpus.Total != 200 || q.GroundTruthItems != 200 || q.ByStatus[domain.ValidationHeuristic] == 0 || q.TestAccuracy == nil {
		t.Fatalf("resumen: %+v", q)
	}
	var knee RuleQuality
	for _, r := range q.Rules {
		if r.RuleID == "FH-POST-001" {
			knee = r
		}
	}
	// La semilla usa 160° y el consenso dice 162°: falsos positivos → precisión < 1, y nunca se revisó.
	if knee.Test.Precision == nil || knee.Test.Precision.Value >= 1 || knee.Alpha == nil || *knee.Alpha != 0.85 {
		t.Fatalf("regla: %+v", knee)
	}
	if !slices.ContainsFunc(knee.NeedsAudit, func(s string) bool { return strings.Contains(s, "nunca revisada") }) {
		t.Fatalf("auditoría: %v", knee.NeedsAudit)
	}
}
