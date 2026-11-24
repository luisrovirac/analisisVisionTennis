//go:build integration

package repository

import (
	"context"
	"fmt"
	"slices"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"tennisvision/api/internal/domain"
	"tennisvision/api/internal/service"
)

type stubSigner struct{}

func (stubSigner) PresignGet(_ context.Context, key string, _ time.Duration) (string, error) {
	return "http://storage/" + key, nil
}

// completedCase crea un análisis de derecha completado y revisable, con la rodilla indicada.
func completedCase(t *testing.T, pool *pgxpool.Pool, owner string, knee float64, fired bool) string {
	t.Helper()
	ctx := context.Background()
	params := domain.AnalysisParams{StrokeType: domain.StrokeForehand, Handedness: domain.HandRight,
		CameraView: domain.ViewSide, ExpertReviewAllowed: true}
	a := domain.NewAnalysis(uuid.NewString(), owner, params, time.Now().UTC())
	if err := NewPostgres(pool).Create(ctx, a); err != nil {
		t.Fatal(err)
	}
	findings := `[]`
	if fired {
		findings = `[{"rule_id":"FH-POST-001"}]`
	}
	report := fmt.Sprintf(`{"stroke_type":"forehand","segment":{"impact_frame":12},"findings":%s,"strengths":[],
"metrics":[{"id":"knee_angle_min","value":%g,"confidence":0.9}]}`, findings, knee)
	if _, err := pool.Exec(ctx, `UPDATE analyses SET status = 'completed', report = $2::jsonb WHERE id = $1::uuid`, a.ID, report); err != nil {
		t.Fatal(err)
	}
	return a.ID
}

func professional(t *testing.T, users *PostgresUsers, roles ...domain.Role) domain.User {
	t.Helper()
	u, err := users.CreateUser(context.Background(), newPrincipal(true))
	if err != nil {
		t.Fatal(err)
	}
	if err := users.GrantRoles(context.Background(), nil, u.ID, roles, "test"); err != nil {
		t.Fatal(err)
	}
	u.Roles = roles
	return u
}

func TestValidationCorpusCampaignAndGroundTruth(t *testing.T) {
	pool := newTestPool(t)
	ctx := context.Background()
	seedKnowledge(t, pool)
	users := NewPostgresUsers(pool)
	repo := NewPostgresValidation(pool)
	svc := service.NewValidation(repo, NewPostgres(pool), NewPostgresKnowledge(pool), stubSigner{}, time.Hour,
		service.ValidationConfig{MinMetricConfidence: 0.5}, time.Now)

	owner := newUser(t)
	coordinator := professional(t, users, domain.RoleValidator)
	raters := []domain.User{professional(t, users, domain.RoleExpert), professional(t, users, domain.RoleExpert),
		professional(t, users, domain.RoleExpert, domain.RoleValidator)}
	stiff := completedCase(t, pool, owner, 172, true)
	bent := completedCase(t, pool, owner, 140, false)

	for _, id := range []string{stiff, bent} {
		if _, err := svc.AddToCorpus(ctx, coordinator, id, "piloto"); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := svc.AddToCorpus(ctx, coordinator, stiff, ""); err == nil {
		t.Fatal("un caso no entra dos veces al corpus")
	}

	// Revisión libre: reenviar reemplaza los ítems, no los duplica.
	v := domain.VerdictCorrect
	r := 2
	free := domain.ReviewInput{Items: []domain.ReviewItem{{RuleID: "FH-POST-001", Rating: &r, Verdict: &v}}}
	for range 2 {
		if _, err := svc.SaveFreeReview(ctx, raters[0], stiff, free); err != nil {
			t.Fatal(err)
		}
	}
	got, err := repo.Reviews(ctx, service.ReviewFilter{AnalysisID: stiff, FreeOnly: true})
	if err != nil || len(got) != 1 || len(got[0].Items) != 1 || got[0].Items[0].SystemFired == nil || !*got[0].Items[0].SystemFired {
		t.Fatalf("revisión libre: %+v %v", got, err)
	}

	ids := make([]string, len(raters))
	for i, u := range raters {
		ids[i] = u.ID
	}
	camp, err := svc.CreateCampaign(ctx, coordinator, domain.CampaignInput{Stroke: domain.StrokeForehand, Title: "Piloto",
		RuleIDs: []string{"FH-POST-001"}, CaseIDs: []string{stiff, bent}, RaterIDs: ids, MaxRounds: 1})
	if err != nil || len(camp.CaseIDs) != 2 || len(camp.RaterIDs) != 3 {
		t.Fatalf("campaña: %+v %v", camp, err)
	}
	ratings := map[string]int{stiff: 2, bent: 6}
	for _, u := range raters {
		for caseID, score := range ratings {
			s := score
			in := domain.ReviewInput{Items: []domain.ReviewItem{{RuleID: "FH-POST-001", Rating: &s}}}
			if _, err := svc.SaveCampaignReview(ctx, u, camp.ID, caseID, in); err != nil {
				t.Fatal(err)
			}
		}
	}
	closed, err := svc.Close(ctx, coordinator, camp.ID)
	if err != nil || closed.Status != domain.CampaignClosed || closed.Results == nil || closed.ClosedAt == nil {
		t.Fatalf("cierre: %+v %v", closed, err)
	}
	labeled, err := repo.LabeledSamples(ctx, domain.StrokeForehand, "")
	if err != nil {
		t.Fatal(err)
	}
	present := map[string]bool{}
	for _, ls := range labeled {
		if ls.RuleID == "FH-POST-001" {
			present[ls.ID] = ls.Present
		}
	}
	if !present[stiff] || present[bent] || len(present) < 2 {
		t.Fatalf("ground truth: %+v", present)
	}
	corpus, _ := svc.Corpus(ctx, domain.StrokeForehand)
	if i := slices.IndexFunc(corpus, func(e domain.CorpusEntry) bool { return e.AnalysisID == stiff }); i < 0 || corpus[i].GroundTruth != 1 {
		t.Fatalf("corpus con ground truth: %+v", corpus)
	}
	if _, err := svc.Quality(ctx, domain.StrokeForehand); err != nil {
		t.Fatal(err)
	}

	// Si el dueño retira el permiso, el caso sale del corpus y del ground truth utilizable.
	if _, err := NewPostgres(pool).SetExpertReview(ctx, owner, stiff, false); err != nil {
		t.Fatal(err)
	}
	labeled, _ = repo.LabeledSamples(ctx, domain.StrokeForehand, "")
	if slices.ContainsFunc(labeled, func(ls domain.LabeledSample) bool { return ls.ID == stiff }) {
		t.Fatal("un caso revocado no se usa para calibrar")
	}
}

func TestApprovalPersistsChangelogAndBump(t *testing.T) {
	pool := newTestPool(t)
	ctx := context.Background()
	base := seedKnowledge(t, pool)
	users := NewPostgresUsers(pool)
	expert := professional(t, users, domain.RoleExpert)
	validator := professional(t, users, domain.RoleValidator)
	svc := service.NewKnowledge(NewPostgresKnowledge(pool), service.KnowledgeConfig{MinMetricConfidence: 0.5}, time.Now)

	i := slices.IndexFunc(base.KB.Rules, func(r domain.Rule) bool { return r.ID == "FH-POST-001" })
	rule := base.KB.Rules[i]
	rule.Correction = "Flexiona más las rodillas en la preparación."
	p, err := svc.CreateProposal(ctx, expert, domain.ProposalInput{Stroke: domain.StrokeForehand, Title: "Texto",
		Rationale: "más claro", Changes: []domain.Change{{Op: domain.OpUpsertRule, Rule: &rule}}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := svc.Submit(ctx, expert, p.ID); err != nil {
		t.Fatal(err)
	}
	rel, err := svc.Approve(ctx, validator, p.ID, "ok")
	if err != nil {
		t.Fatal(err)
	}
	want, _ := domain.BumpVersion(base.Version, domain.BumpPatch)
	if rel.Version != want || rel.Bump == nil || *rel.Bump != domain.BumpPatch || len(rel.Entries) != 1 ||
		rel.Entries[0].Fields[0] != "correction" || rel.ValidatorName == "" {
		t.Fatalf("versión: %+v", rel)
	}
	j := slices.IndexFunc(rel.KB.Rules, func(r domain.Rule) bool { return r.ID == "FH-POST-001" })
	if got := rel.KB.Rules[j]; got.ValidationStatus != domain.ValidationExpertReviewed || got.Validation == nil ||
		got.Validation.ValidatedBy[0] != validator.ID {
		t.Fatalf("traza: %+v", got)
	}
}
