package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"testing"
	"time"

	"tennisvision/api/internal/domain"
)

// fakeKnowledgeRepo reproduce en memoria las garantías del repositorio real (estado esperado,
// cuatro ojos, una sola versión activa por golpe).
type fakeKnowledgeRepo struct {
	catalog   []domain.MetricInfo
	releases  []domain.Release
	proposals map[string]domain.Proposal
	comments  []domain.Comment
	samples   []domain.MetricSample
	audits    []string
	seq       int
}

func newFakeKnowledgeRepo(t *testing.T) *fakeKnowledgeRepo {
	t.Helper()
	read := func(name string, dst any) {
		raw, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "fixtures", name))
		if err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(raw, dst); err != nil {
			t.Fatal(err)
		}
	}
	var kb domain.KnowledgeBase
	r := &fakeKnowledgeRepo{proposals: map[string]domain.Proposal{}}
	read("knowledge_base.example.json", &kb)
	read("metric_catalog.example.json", &r.catalog)
	r.releases = []domain.Release{{ID: r.id(), Stroke: kb.Stroke, Version: kb.Version, KB: kb, Active: true, Status: domain.ReleaseValid}}
	return r
}

func (r *fakeKnowledgeRepo) id() string {
	r.seq++
	return fmt.Sprintf("00000000-0000-4000-8000-%012d", r.seq)
}

func (r *fakeKnowledgeRepo) Catalog(context.Context, domain.StrokeType) ([]domain.MetricInfo, error) {
	return r.catalog, nil
}

func (r *fakeKnowledgeRepo) ActiveRelease(_ context.Context, stroke domain.StrokeType) (domain.Release, error) {
	for _, rel := range r.releases {
		if rel.Stroke == stroke && rel.Active {
			return rel, nil
		}
	}
	return domain.Release{}, domain.ErrResourceNotFound
}

func (r *fakeKnowledgeRepo) Release(_ context.Context, id string) (domain.Release, error) {
	for _, rel := range r.releases {
		if rel.ID == id {
			return rel, nil
		}
	}
	return domain.Release{}, domain.ErrResourceNotFound
}

func (r *fakeKnowledgeRepo) Releases(context.Context, domain.StrokeType, int) ([]domain.Release, error) {
	return r.releases, nil
}

func (r *fakeKnowledgeRepo) CreateProposal(_ context.Context, p domain.Proposal) (domain.Proposal, error) {
	p.ID = r.id()
	r.proposals[p.ID] = p
	return p, nil
}

func (r *fakeKnowledgeRepo) Proposal(_ context.Context, id string) (domain.Proposal, error) {
	p, ok := r.proposals[id]
	if !ok {
		return domain.Proposal{}, domain.ErrResourceNotFound
	}
	return p, nil
}

func (r *fakeKnowledgeRepo) Proposals(context.Context, ProposalFilter) ([]domain.Proposal, error) {
	return nil, nil
}

func (r *fakeKnowledgeRepo) UpdateProposal(_ context.Context, p domain.Proposal, from ...domain.ProposalStatus) (bool, error) {
	if !slices.Contains(from, r.proposals[p.ID].Status) {
		return false, nil
	}
	r.proposals[p.ID] = p
	return true, nil
}

func (r *fakeKnowledgeRepo) Decide(_ context.Context, id, decider string, to domain.ProposalStatus, note string) (bool, error) {
	p := r.proposals[id]
	if p.Status != domain.ProposalSubmitted || p.AuthorID == decider {
		return false, nil
	}
	p.Status, p.DecidedBy, p.DecisionNote = to, &decider, note
	r.proposals[id] = p
	return true, nil
}

func (r *fakeKnowledgeRepo) Approve(ctx context.Context, id, validator, note string, build ApproveFunc) (domain.Release, error) {
	p := r.proposals[id]
	if p.Status != domain.ProposalSubmitted || p.AuthorID == validator {
		return domain.Release{}, domain.ErrConflict
	}
	active, err := r.ActiveRelease(ctx, p.Stroke)
	if err != nil {
		return domain.Release{}, err
	}
	kb, changelog, err := build(active, p, r.catalog)
	if err != nil {
		return domain.Release{}, err
	}
	for i := range r.releases {
		r.releases[i].Active = false
	}
	rel := domain.Release{ID: r.id(), Stroke: p.Stroke, Version: kb.Version, KB: kb, Changelog: changelog,
		ProposalID: &p.ID, ValidatedBy: &validator, Active: true, Status: domain.ReleaseValid}
	r.releases = append(r.releases, rel)
	p.Status, p.DecidedBy, p.ReleaseID = domain.ProposalApproved, &validator, &rel.ID
	r.proposals[id] = p
	return rel, nil
}

func (r *fakeKnowledgeRepo) Activate(_ context.Context, releaseID, _ string) (domain.Release, error) {
	var out domain.Release
	for i := range r.releases {
		r.releases[i].Active = r.releases[i].ID == releaseID
		if r.releases[i].Active {
			out = r.releases[i]
		}
	}
	return out, nil
}

func (r *fakeKnowledgeRepo) AddComment(_ context.Context, c domain.Comment) (domain.Comment, error) {
	r.comments = append(r.comments, c)
	return c, nil
}

func (r *fakeKnowledgeRepo) Comments(context.Context, string) ([]domain.Comment, error) {
	return r.comments, nil
}

func (r *fakeKnowledgeRepo) RecentSamples(context.Context, domain.StrokeType, int) ([]domain.MetricSample, error) {
	return r.samples, nil
}

func (r *fakeKnowledgeRepo) CreateMetricRequest(_ context.Context, m domain.MetricRequest) (domain.MetricRequest, error) {
	return m, nil
}

func (r *fakeKnowledgeRepo) MetricRequests(context.Context, int) ([]domain.MetricRequest, error) {
	return nil, nil
}

func (r *fakeKnowledgeRepo) Record(_ context.Context, _ *string, action, _ string, _ any) error {
	r.audits = append(r.audits, action)
	return nil
}

var (
	ana   = domain.User{ID: "aaaaaaaa-0000-4000-8000-00000000000a", Roles: []domain.Role{domain.RoleExpert, domain.RoleValidator}}
	vicky = domain.User{ID: "bbbbbbbb-0000-4000-8000-00000000000b", Roles: []domain.Role{domain.RoleValidator}}
)

// Propuesta: subir el umbral de "piernas rígidas" de 160° a 165°.
func kneeProposal() domain.ProposalInput {
	rule := domain.Rule{ID: "FH-POST-001", Title: "Piernas rígidas", Category: "fundamental", Impact: 4, Difficulty: 1,
		When:        domain.ConditionGroup{All: []domain.Condition{{Metric: "knee_angle_min", Op: "gt", Value: domain.Threshold{Value: 165}}}},
		Explanation: "e", Correction: "c"}
	return domain.ProposalInput{Stroke: domain.StrokeForehand, Title: "Umbral de rodilla", Rationale: "Muchos falsos positivos",
		Changes: []domain.Change{{Op: domain.OpUpsertRule, Rule: &rule}}}
}

func newKnowledgeSvc(t *testing.T) (*KnowledgeService, *fakeKnowledgeRepo) {
	repo := newFakeKnowledgeRepo(t)
	return NewKnowledge(repo, KnowledgeConfig{MinMetricConfidence: 0.5}, time.Now), repo
}

func submitted(t *testing.T, svc *KnowledgeService, author domain.User) domain.Proposal {
	t.Helper()
	p, err := svc.CreateProposal(context.Background(), author, kneeProposal())
	if err != nil {
		t.Fatal(err)
	}
	if p, err = svc.Submit(context.Background(), author, p.ID); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestApprovalPublishesNewValidatedVersion(t *testing.T) {
	svc, repo := newKnowledgeSvc(t)
	p := submitted(t, svc, ana)
	rel, err := svc.Approve(context.Background(), vicky, p.ID, "ok")
	if err != nil {
		t.Fatal(err)
	}
	if rel.Version != "0.3.0" || !rel.Active || len(repo.releases) != 2 || repo.releases[0].Active {
		t.Fatalf("versión publicada incorrecta: %+v", rel)
	}
	i := slices.IndexFunc(rel.KB.Rules, func(r domain.Rule) bool { return r.ID == "FH-POST-001" })
	if !rel.KB.Rules[i].Validated || rel.KB.Rules[i].When.All[0].Value.Value != 165 {
		t.Fatalf("la regla aprobada debe quedar validada y con el umbral nuevo: %+v", rel.KB.Rules[i])
	}
	if repo.proposals[p.ID].Status != domain.ProposalApproved {
		t.Fatal("la propuesta debe quedar aprobada")
	}
}

func TestNobodyValidatesTheirOwnProposal(t *testing.T) {
	svc, _ := newKnowledgeSvc(t)
	p := submitted(t, svc, ana) // ana es experta Y validadora
	for name, act := range map[string]func() error{
		"aprobar":       func() error { _, err := svc.Approve(context.Background(), ana, p.ID, ""); return err },
		"rechazar":      func() error { _, err := svc.Reject(context.Background(), ana, p.ID, "no"); return err },
		"pedir cambios": func() error { _, err := svc.RequestChanges(context.Background(), ana, p.ID, "x"); return err },
	} {
		if err := act(); !errors.Is(err, domain.ErrForbidden) {
			t.Errorf("%s lo propio: err=%v", name, err)
		}
	}
}

func TestStaleProposalCannotBeApproved(t *testing.T) {
	svc, repo := newKnowledgeSvc(t)
	first, second := submitted(t, svc, ana), submitted(t, svc, ana)
	if _, err := svc.Approve(context.Background(), vicky, first.ID, ""); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.Approve(context.Background(), vicky, second.ID, ""); !errors.Is(err, domain.ErrConflict) {
		t.Fatalf("la segunda se escribió sobre una base que ya cambió: err=%v", err)
	}
	if len(repo.releases) != 2 {
		t.Fatal("no debe publicarse nada en un conflicto")
	}
}

func TestSubmitRejectsUnknownMetric(t *testing.T) {
	svc, _ := newKnowledgeSvc(t)
	in := kneeProposal()
	in.Changes[0].Rule.When.All[0].Metric = "racket_speed" // aún no la mide el worker
	p, err := svc.CreateProposal(context.Background(), ana, in)
	if err != nil {
		t.Fatal(err) // un borrador se puede guardar…
	}
	if _, err := svc.Submit(context.Background(), ana, p.ID); !errors.Is(err, domain.ErrInvalidInput) {
		t.Fatalf("…pero no enviar: err=%v", err)
	}
}

func TestRequestChangesThenResubmit(t *testing.T) {
	svc, _ := newKnowledgeSvc(t)
	p := submitted(t, svc, ana)
	if _, err := svc.RequestChanges(context.Background(), vicky, p.ID, ""); !errors.Is(err, domain.ErrInvalidInput) {
		t.Fatal("pedir cambios exige una nota para el experto")
	}
	if _, err := svc.RequestChanges(context.Background(), vicky, p.ID, "Justifica con casos"); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.UpdateProposal(context.Background(), vicky, p.ID, kneeProposal()); !errors.Is(err, domain.ErrForbidden) {
		t.Fatal("solo el autor edita")
	}
	in := kneeProposal()
	in.Evidence = "12 casos revisados"
	if _, err := svc.UpdateProposal(context.Background(), ana, p.ID, in); err != nil {
		t.Fatal(err)
	}
	if again, err := svc.Submit(context.Background(), ana, p.ID); err != nil || again.Status != domain.ProposalSubmitted {
		t.Fatalf("reenvío: %+v %v", again, err)
	}
}

func TestProposalDetailShowsImpactBeforeAndAfter(t *testing.T) {
	svc, repo := newKnowledgeSvc(t)
	repo.samples = []domain.MetricSample{
		{"knee_angle_min": {Value: 162, Confidence: 0.9}}, // dispara con 160, no con 165
		{"knee_angle_min": {Value: 170, Confidence: 0.9}}, // dispara con ambos
	}
	p := submitted(t, svc, ana)
	d, err := svc.Proposal(context.Background(), p.ID)
	if err != nil || d.Problems != "" || len(d.Impacts) != 1 {
		t.Fatalf("detalle: %+v %v", d, err)
	}
	imp := d.Impacts[0]
	if imp.Before == nil || imp.Before.Fired != 2 || imp.After == nil || imp.After.Fired != 1 {
		t.Fatalf("impacto: before=%+v after=%+v", imp.Before, imp.After)
	}
}

func TestRollbackActivatesPreviousVersion(t *testing.T) {
	svc, repo := newKnowledgeSvc(t)
	original := repo.releases[0].ID
	if _, err := svc.Approve(context.Background(), vicky, submitted(t, svc, ana).ID, ""); err != nil {
		t.Fatal(err)
	}
	rel, err := svc.Activate(context.Background(), vicky, original)
	if err != nil || !rel.Active || rel.Version != "0.2.0" {
		t.Fatalf("rollback: %+v %v", rel, err)
	}
}
