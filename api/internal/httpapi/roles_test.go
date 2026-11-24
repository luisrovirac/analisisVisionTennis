package httpapi

import (
	"context"
	"fmt"
	"net/http"
	"testing"

	"tennisvision/api/internal/domain"
	"tennisvision/api/internal/service"
)

// Los stubs embeben la interfaz: un método no previsto en el test entra en pánico (500), nunca 403.
type stubKnowledge struct{ KnowledgeUseCases }

func (stubKnowledge) Catalog(context.Context, domain.StrokeType) ([]domain.MetricInfo, error) {
	return []domain.MetricInfo{{Stroke: "forehand", ID: "knee_angle_min"}}, nil
}

func (stubKnowledge) CreateProposal(_ context.Context, author domain.User, in domain.ProposalInput) (domain.Proposal, error) {
	return domain.Proposal{ID: "p1", AuthorID: author.ID, ProposalInput: in, Status: domain.ProposalDraft}, nil
}

func (stubKnowledge) Approve(context.Context, domain.User, string, string) (domain.Release, error) {
	return domain.Release{ID: "r2", Version: "0.3.0", Active: true}, nil
}

func (stubKnowledge) Activate(context.Context, domain.User, string) (domain.Release, error) {
	return domain.Release{ID: "r1", Active: true}, nil
}

type stubValidation struct{ ValidationUseCases }

func (stubValidation) Quality(_ context.Context, stroke domain.StrokeType) (service.QualityReport, error) {
	return service.QualityReport{Stroke: stroke}, nil
}

func (stubValidation) SaveFreeReview(_ context.Context, u domain.User, caseID string, in domain.ReviewInput) (domain.CaseReview, error) {
	return domain.CaseReview{ID: "rv1", AnalysisID: caseID, ReviewerID: u.ID, Items: in.Items}, nil
}

func (stubValidation) CreateCampaign(_ context.Context, _ domain.User, in domain.CampaignInput) (domain.Campaign, error) {
	return domain.Campaign{ID: "c1", Stroke: in.Stroke, Status: domain.CampaignOpen, CurrentRound: 1}, nil
}

func (stubValidation) AddToCorpus(_ context.Context, _ domain.User, id, _ string) (domain.CorpusEntry, error) {
	return domain.CorpusEntry{AnalysisID: id, Split: domain.SplitTest}, nil
}

type stubAdmin struct{ AdminUseCases }

func (stubAdmin) ListUsers(context.Context, string) ([]domain.User, error) { return nil, nil }

func (stubAdmin) Invite(context.Context, domain.User, string, []string) (service.InviteResult, error) {
	return service.InviteResult{Invitation: &domain.Invitation{ID: "i1", Email: "x@x.test"}}, nil
}

type stubProfiles struct{}

func (stubProfiles) Get(context.Context, string) (*domain.Profile, error) {
	return &domain.Profile{DisplayName: "Ana", Specialty: "biomechanics"}, nil
}

func (stubProfiles) Save(_ context.Context, u domain.User, p domain.Profile) (domain.Profile, error) {
	if !u.Has(domain.RoleExpert, domain.RoleValidator) {
		return domain.Profile{}, domain.ErrForbidden
	}
	return p, nil
}

func routerAs(roles ...domain.Role) http.Handler {
	return newRouterWithUsers(&stubUseCases{}, fakeUsers{roles: roles})
}

func TestRoleMatrix(t *testing.T) {
	const proposal = `{"stroke":"forehand","title":"t","rationale":"r","changes":[{"op":"remove_rule","rule_id":"FH-X-1"}]}`
	routes := []struct {
		method, path, body string
		allowed            []domain.Role // vacío = solo comprobar que un usuario normal no entra
	}{
		{http.MethodGet, "/v1/kb/metrics", "", []domain.Role{domain.RoleExpert, domain.RoleValidator, domain.RoleAdmin}},
		{http.MethodPost, "/v1/expert/proposals", proposal, []domain.Role{domain.RoleExpert}},
		{http.MethodPost, "/v1/validator/proposals/p1/approve", `{"note":""}`, []domain.Role{domain.RoleValidator}},
		{http.MethodPost, "/v1/kb/releases/r1/activate", "", []domain.Role{domain.RoleValidator, domain.RoleAdmin}},
		{http.MethodGet, "/v1/admin/users", "", []domain.Role{domain.RoleAdmin}},
		{http.MethodGet, "/v1/kb/quality?stroke=forehand", "", []domain.Role{domain.RoleExpert, domain.RoleValidator, domain.RoleAdmin}},
		{http.MethodPut, "/v1/kb/cases/a1/review", `{"items":[{"rule_id":"FH-X-001","rating":3}]}`,
			[]domain.Role{domain.RoleExpert, domain.RoleValidator}},
		{http.MethodPost, "/v1/kb/campaigns", `{"stroke":"forehand"}`, []domain.Role{domain.RoleValidator, domain.RoleAdmin}},
		{http.MethodPost, "/v1/kb/corpus", `{"analysis_id":"a1"}`, []domain.Role{domain.RoleValidator, domain.RoleAdmin}},
		{http.MethodPost, "/v1/admin/invitations", `{"email":"x@x.test","roles":["expert"]}`, []domain.Role{domain.RoleAdmin}},
	}
	all := [][]domain.Role{nil, {domain.RoleExpert}, {domain.RoleValidator}, {domain.RoleAdmin},
		{domain.RoleExpert, domain.RoleValidator}}
	for _, rt := range routes {
		for _, roles := range all {
			want := domain.User{Roles: roles}.Has(rt.allowed...)
			t.Run(fmt.Sprintf("%s %s como %v", rt.method, rt.path, roles), func(t *testing.T) {
				rec := do(routerAs(roles...), rt.method, rt.path, rt.body)
				if got := rec.Code != http.StatusForbidden; got != want {
					t.Fatalf("permitido=%v, se esperaba %v (code=%d body=%s)", got, want, rec.Code, rec.Body)
				}
				if want && rec.Code >= 400 {
					t.Fatalf("permitido pero falló: code=%d body=%s", rec.Code, rec.Body)
				}
			})
		}
	}
}

func TestSuspendedAccountIsBlockedEverywhere(t *testing.T) {
	h := newRouterWithUsers(&stubUseCases{}, fakeUsers{status: domain.UserSuspended, roles: []domain.Role{domain.RoleAdmin}})
	for _, path := range []string{"/v1/analyses", "/v1/me", "/v1/admin/users"} {
		rec := do(h, http.MethodGet, path, "")
		if rec.Code != http.StatusForbidden || decode(t, rec)["error"].(map[string]any)["code"] != "ACCOUNT_SUSPENDED" {
			t.Fatalf("%s: code=%d body=%s", path, rec.Code, rec.Body)
		}
	}
}

func TestMeReturnsRolesAndProfileForProfessionals(t *testing.T) {
	body := decode(t, do(routerAs(domain.RoleExpert), http.MethodGet, "/v1/me", ""))
	if fmt.Sprint(body["roles"]) != "[expert]" || body["profile"] == nil {
		t.Fatalf("body=%v", body)
	}
	body = decode(t, do(routerAs(), http.MethodGet, "/v1/me", ""))
	if fmt.Sprint(body["roles"]) != "[]" || body["profile"] != nil {
		t.Fatalf("un usuario normal no tiene roles ni perfil: %v", body)
	}
	if rec := do(routerAs(), http.MethodPut, "/v1/me/profile", `{"display_name":"x","specialty":"physio"}`); rec.Code != http.StatusForbidden {
		t.Fatalf("perfil profesional para un usuario normal: code=%d", rec.Code)
	}
}

func TestStrictJSONRejectsUnknownFields(t *testing.T) {
	rec := do(routerAs(domain.RoleExpert), http.MethodPost, "/v1/expert/proposals", `{"stroke":"forehand","titel":"typo"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("code=%d body=%s", rec.Code, rec.Body)
	}
}

func TestCreateDefaultsExpertReviewToAllowed(t *testing.T) {
	uc := &stubUseCases{}
	do(newTestRouter(uc), http.MethodPost, "/v1/analyses", `{"stroke_type":"forehand"}`)
	if !uc.gotParam.ExpertReviewAllowed {
		t.Fatal("el checkbox viene marcado por defecto")
	}
	do(newTestRouter(uc), http.MethodPost, "/v1/analyses", `{"stroke_type":"forehand","expert_review_allowed":false}`)
	if uc.gotParam.ExpertReviewAllowed {
		t.Fatal("el usuario puede desmarcarlo")
	}
}

func TestPatchAnalysisTogglesExpertReview(t *testing.T) {
	uc := &stubUseCases{analysis: domain.Analysis{ID: "a1", Status: domain.StatusCompleted}}
	rec := do(newTestRouter(uc), http.MethodPatch, "/v1/analyses/a1", `{"expert_review_allowed":false}`)
	if rec.Code != http.StatusOK || decode(t, rec)["expert_review_allowed"] != false {
		t.Fatalf("code=%d body=%s", rec.Code, rec.Body)
	}
	if rec := do(newTestRouter(uc), http.MethodPatch, "/v1/analyses/a1", `{}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("sin el campo: code=%d", rec.Code)
	}
}
