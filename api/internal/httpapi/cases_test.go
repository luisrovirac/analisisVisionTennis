package httpapi

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"

	"tennisvision/api/internal/domain"
	"tennisvision/api/internal/service"
)

const caseID = "cccccccc-0000-4000-8000-000000000001"

type stubCases struct{}

func caseAnalysis() domain.Analysis {
	owner := "aaaaaaaa-0000-4000-8000-000000000001"
	return domain.Analysis{
		ID: caseID, UserID: &owner, Status: domain.StatusCompleted,
		Params: domain.AnalysisParams{Handedness: domain.HandRight, CameraView: domain.ViewSide},
		Report: []byte(`{"stroke_type":"forehand","findings":[{"rule_id":"FH-1"}],"strengths":[{"rule_id":"FH-S-1"}]}`),
	}
}

func (stubCases) List(context.Context, domain.CaseFilter) (service.Page, error) {
	return service.Page{Items: []domain.Analysis{caseAnalysis()}}, nil
}

func (stubCases) Get(_ context.Context, id string) (service.CaseDetail, error) {
	if id != caseID {
		return service.CaseDetail{}, domain.ErrNotFound
	}
	return service.CaseDetail{Analysis: caseAnalysis(), Media: domain.MediaLinks{
		VideoURL: "http://v", PoseURL: "http://p", ExpiresAt: time.Unix(0, 0)}}, nil
}

func TestCasesAreOnlyForProfessionals(t *testing.T) {
	for _, path := range []string{"/v1/kb/cases", "/v1/kb/cases/" + caseID} {
		if rec := do(routerAs(), http.MethodGet, path, ""); rec.Code != http.StatusForbidden {
			t.Fatalf("%s como jugador: code=%d", path, rec.Code)
		}
		for _, role := range []domain.Role{domain.RoleExpert, domain.RoleValidator, domain.RoleAdmin} {
			if rec := do(routerAs(role), http.MethodGet, path, ""); rec.Code != http.StatusOK {
				t.Fatalf("%s como %s: code=%d body=%s", path, role, rec.Code, rec.Body)
			}
		}
	}
}

func TestCasesListShowsFiredRulesWithoutOwner(t *testing.T) {
	rec := do(routerAs(domain.RoleExpert), http.MethodGet, "/v1/kb/cases?stroke=forehand&rule_id=FH-1", "")
	body := rec.Body.String()
	if rec.Code != http.StatusOK || !strings.Contains(body, `"finding_rule_ids":["FH-1"]`) ||
		!strings.Contains(body, `"strength_rule_ids":["FH-S-1"]`) || !strings.Contains(body, `"stroke_type":"forehand"`) {
		t.Fatalf("respuesta inesperada: %d %s", rec.Code, body)
	}
	if strings.Contains(body, "user") || strings.Contains(body, "aaaaaaaa") {
		t.Fatalf("el listado no debe exponer al dueño: %s", body)
	}
}

func TestCaseDetailCarriesMediaWithoutOwner(t *testing.T) {
	rec := do(routerAs(domain.RoleExpert), http.MethodGet, "/v1/kb/cases/"+caseID, "")
	body := rec.Body.String()
	if rec.Code != http.StatusOK || !strings.Contains(body, `"video_url":"http://v"`) || !strings.Contains(body, `"pose_url":"http://p"`) {
		t.Fatalf("respuesta inesperada: %d %s", rec.Code, body)
	}
	if strings.Contains(body, "user_id") || strings.Contains(body, "aaaaaaaa") {
		t.Fatalf("el detalle no debe exponer al dueño: %s", body)
	}
}

func TestCasesRejectInvalidFilters(t *testing.T) {
	for _, q := range []string{"stroke=smash", "limit=abc", "limit=0x", "rule_id=" + strings.Repeat("x", 101)} {
		if rec := do(routerAs(domain.RoleExpert), http.MethodGet, "/v1/kb/cases?"+q, ""); rec.Code != http.StatusBadRequest {
			t.Fatalf("%s: code=%d", q, rec.Code)
		}
	}
}

func TestRevokedCaseIsNotFound(t *testing.T) {
	rec := do(routerAs(domain.RoleExpert), http.MethodGet, "/v1/kb/cases/dddddddd-0000-4000-8000-000000000009", "")
	if rec.Code != http.StatusNotFound {
		t.Fatalf("code=%d", rec.Code)
	}
}
