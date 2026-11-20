package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"testing/fstest"
	"time"

	"tennisvision/api/internal/domain"
	"tennisvision/api/internal/service"
)

const (
	validToken = "token-de-alice"
	aliceID    = "aaaaaaaa-0000-4000-8000-000000000001"
)

type stubUseCases struct {
	analysis domain.Analysis
	page     service.Page
	err      error
	gotParam domain.AnalysisParams
	gotOwner string
	gotQuery domain.ListQuery
}

func (s *stubUseCases) Create(_ context.Context, owner string, p domain.AnalysisParams) (service.UploadTicket, error) {
	s.gotOwner, s.gotParam = owner, p
	return service.UploadTicket{Analysis: s.analysis, UploadURL: "http://up", ExpiresAt: time.Unix(0, 0)}, s.err
}

func (s *stubUseCases) Submit(_ context.Context, owner, _ string) (domain.Analysis, error) {
	s.gotOwner = owner
	return s.analysis, s.err
}

func (s *stubUseCases) Get(_ context.Context, owner, _ string) (domain.Analysis, error) {
	s.gotOwner = owner
	return s.analysis, s.err
}

func (s *stubUseCases) List(_ context.Context, owner string, q domain.ListQuery) (service.Page, error) {
	s.gotOwner, s.gotQuery = owner, q
	return s.page, s.err
}

func (s *stubUseCases) Delete(_ context.Context, owner, _ string) error {
	s.gotOwner = owner
	return s.err
}

func (s *stubUseCases) SetExpertReview(_ context.Context, owner, _ string, allowed bool) (domain.Analysis, error) {
	s.gotOwner = owner
	s.analysis.Params.ExpertReviewAllowed = allowed
	return s.analysis, s.err
}

type stubPreferences struct {
	stored   domain.Preferences
	err      error
	gotOwner string
}

func (s *stubPreferences) Get(_ context.Context, owner string) (domain.Preferences, error) {
	s.gotOwner = owner
	return s.stored, s.err
}

func (s *stubPreferences) Update(_ context.Context, owner string, p domain.Preferences) (domain.Preferences, error) {
	s.gotOwner = owner
	if s.err != nil {
		return domain.Preferences{}, s.err
	}
	s.stored = p
	return p, nil
}

// fakeVerifier acepta solo validToken; cualquier otro valor se comporta como un token inválido.
type fakeVerifier struct{}

func (fakeVerifier) Verify(_ context.Context, raw string) (domain.Principal, error) {
	if raw != validToken {
		return domain.Principal{}, fmt.Errorf("%w: firma inválida", domain.ErrUnauthenticated)
	}
	return domain.Principal{Issuer: "https://idp", Subject: "alice"}, nil
}

type fakeUsers struct {
	err    error
	roles  []domain.Role
	status domain.UserStatus
}

func (u fakeUsers) Resolve(context.Context, domain.Principal) (domain.User, error) {
	status := u.status
	if status == "" {
		status = domain.UserActive
	}
	return domain.User{ID: aliceID, Email: "alice@example.test", Roles: u.roles, Status: status}, u.err
}

func newTestRouter(uc AnalysisUseCases, checks ...HealthCheck) http.Handler {
	return newRouterWithUsers(uc, fakeUsers{}, checks...)
}

func newRouterWithUsers(uc AnalysisUseCases, users UserResolver, checks ...HealthCheck) http.Handler {
	return newRouter(uc, &stubPreferences{}, users, checks...)
}

func newRouter(uc AnalysisUseCases, prefs PreferenceUseCases, users UserResolver, checks ...HealthCheck) http.Handler {
	return NewRouter(RouterDeps{
		Analyses:    uc,
		Preferences: prefs,
		Profiles:    stubProfiles{},
		Admin:       stubAdmin{},
		Knowledge:   stubKnowledge{},
		Verifier:    fakeVerifier{},
		Users:       users,
		AuthConfig:  ClientAuthConfig{Enabled: true, Issuer: "https://idp", ClientID: "tennis-web"},
		Checks:      checks,
		Web:         fstest.MapFS{"index.html": {Data: []byte("<h1>tenis</h1>")}},
		Logger:      slog.New(slog.NewTextHandler(io.Discard, nil)),
	})
}

// do envía la petición autenticada como alice.
func do(h http.Handler, method, path, body string) *httptest.ResponseRecorder {
	return doWithAuth(h, method, path, body, "Bearer "+validToken)
}

func doWithAuth(h http.Handler, method, path, body, authorization string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if authorization != "" {
		req.Header.Set("Authorization", authorization)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func decode(t *testing.T, rec *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &m); err != nil {
		t.Fatalf("respuesta no es JSON: %s", rec.Body.String())
	}
	return m
}

func TestCreateReturns201WithUploadURL(t *testing.T) {
	uc := &stubUseCases{analysis: domain.Analysis{ID: "a1", Status: domain.StatusAwaitingUpload}}
	rec := do(newTestRouter(uc), http.MethodPost, "/v1/analyses", `{"stroke_type":"forehand","handedness":"left"}`)
	if rec.Code != http.StatusCreated {
		t.Fatalf("code=%d body=%s", rec.Code, rec.Body)
	}
	body := decode(t, rec)
	if body["upload_url"] != "http://up" || uc.gotParam.Handedness != domain.HandLeft || uc.gotOwner != aliceID {
		t.Fatalf("respuesta inesperada: %v / %+v", body, uc.gotParam)
	}
}

func TestCreateRejectsMalformedJSON(t *testing.T) {
	rec := do(newTestRouter(&stubUseCases{}), http.MethodPost, "/v1/analyses", `{bad`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("code=%d", rec.Code)
	}
}

func TestDomainErrorsMapToHTTP(t *testing.T) {
	cases := map[error]int{
		domain.ErrNotFound:         http.StatusNotFound,
		domain.ErrUploadMissing:    http.StatusConflict,
		domain.ErrUploadTooLarge:   http.StatusRequestEntityTooLarge,
		domain.ErrUnsupportedMedia: http.StatusUnsupportedMediaType,
		errors.New("boom"):         http.StatusInternalServerError,
	}
	for err, want := range cases {
		rec := do(newTestRouter(&stubUseCases{err: err}), http.MethodPost, "/v1/analyses/x/submit", "")
		if rec.Code != want {
			t.Errorf("%v → %d, want %d", err, rec.Code, want)
		}
		if decode(t, rec)["error"] == nil {
			t.Errorf("%v: falta el cuerpo de error", err)
		}
	}
}

func TestGetReturnsReportAndError(t *testing.T) {
	code, msg := "NO_PERSON_DETECTED", "No vemos al jugador"
	uc := &stubUseCases{analysis: domain.Analysis{
		ID: "a1", Status: domain.StatusFailed, ErrorCode: &code, ErrorMessage: &msg,
		Report: []byte(`{"findings":[]}`),
	}}
	rec := do(newTestRouter(uc), http.MethodGet, "/v1/analyses/a1", "")
	body := decode(t, rec)
	errBody, _ := body["error"].(map[string]any)
	if rec.Code != http.StatusOK || errBody["code"] != code || body["report"] == nil {
		t.Fatalf("respuesta inesperada: %d %v", rec.Code, body)
	}
}

func TestReadyzReportsFailingDependency(t *testing.T) {
	ok := HealthCheck{Name: "db", Ping: func(context.Context) error { return nil }}
	bad := HealthCheck{Name: "redis", Ping: func(context.Context) error { return errors.New("down") }}
	if rec := do(newTestRouter(&stubUseCases{}, ok), http.MethodGet, "/readyz", ""); rec.Code != http.StatusOK {
		t.Fatalf("readyz sano: %d", rec.Code)
	}
	if rec := do(newTestRouter(&stubUseCases{}, ok, bad), http.MethodGet, "/readyz", ""); rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("readyz con fallo: %d", rec.Code)
	}
}

func TestServesWebClient(t *testing.T) {
	rec := do(newTestRouter(&stubUseCases{}), http.MethodGet, "/", "")
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "tenis") {
		t.Fatalf("code=%d body=%s", rec.Code, rec.Body)
	}
}

func TestProtectedRoutesRejectMissingOrInvalidToken(t *testing.T) {
	uc := &stubUseCases{}
	h := newTestRouter(uc)
	routes := []struct{ method, path string }{
		{http.MethodGet, "/v1/analyses"},
		{http.MethodPost, "/v1/analyses"},
		{http.MethodGet, "/v1/analyses/a1"},
		{http.MethodPost, "/v1/analyses/a1/submit"},
		{http.MethodDelete, "/v1/analyses/a1"},
		{http.MethodGet, "/v1/me/preferences"},
		{http.MethodPut, "/v1/me/preferences"},
	}
	for _, rt := range routes {
		for _, authz := range []string{"", "Bearer otro-token", "Basic " + validToken, validToken} {
			rec := doWithAuth(h, rt.method, rt.path, `{}`, authz)
			if rec.Code != http.StatusUnauthorized || rec.Header().Get("WWW-Authenticate") == "" {
				t.Errorf("%s %s con %q → %d", rt.method, rt.path, authz, rec.Code)
			}
		}
	}
	if uc.gotOwner != "" {
		t.Fatal("ningún handler debe ejecutarse sin token válido")
	}
}

func TestUserResolutionFailureIsInternalError(t *testing.T) {
	rec := do(newRouterWithUsers(&stubUseCases{}, fakeUsers{err: errors.New("db caída")}), http.MethodGet, "/v1/analyses", "")
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("code=%d", rec.Code)
	}
}

func TestListReturnsSummariesAndCursor(t *testing.T) {
	uc := &stubUseCases{page: service.Page{
		Items:      []domain.Analysis{{ID: "a1", Status: domain.StatusCompleted, Report: []byte(`{"big":true}`)}},
		NextCursor: "abc",
	}}
	rec := do(newTestRouter(uc), http.MethodGet, "/v1/analyses?limit=5", "")
	body := decode(t, rec)
	items, _ := body["items"].([]any)
	if rec.Code != http.StatusOK || len(items) != 1 || body["next_cursor"] != "abc" || uc.gotQuery.Limit != 5 {
		t.Fatalf("respuesta inesperada: %d %v %+v", rec.Code, body, uc.gotQuery)
	}
	if _, hasReport := items[0].(map[string]any)["report"]; hasReport {
		t.Fatal("el listado no debe incluir el reporte completo")
	}
}

func TestDeleteReturns204(t *testing.T) {
	uc := &stubUseCases{}
	rec := do(newTestRouter(uc), http.MethodDelete, "/v1/analyses/a1", "")
	if rec.Code != http.StatusNoContent || rec.Body.Len() != 0 || uc.gotOwner != aliceID {
		t.Fatalf("code=%d body=%s owner=%s", rec.Code, rec.Body, uc.gotOwner)
	}
}

func TestDeleteBusyIsConflict(t *testing.T) {
	rec := do(newTestRouter(&stubUseCases{err: domain.ErrAnalysisBusy}), http.MethodDelete, "/v1/analyses/a1", "")
	errBody, _ := decode(t, rec)["error"].(map[string]any)
	if rec.Code != http.StatusConflict || errBody["code"] != "ANALYSIS_BUSY" {
		t.Fatalf("code=%d body=%s", rec.Code, rec.Body)
	}
}

func TestPreferencesRoundTrip(t *testing.T) {
	prefs := &stubPreferences{stored: domain.Preferences{Handedness: domain.HandRight}}
	h := newRouter(&stubUseCases{}, prefs, fakeUsers{})
	if rec := do(h, http.MethodPut, "/v1/me/preferences", `{"handedness":"left"}`); rec.Code != http.StatusOK {
		t.Fatalf("PUT code=%d body=%s", rec.Code, rec.Body)
	}
	rec := do(h, http.MethodGet, "/v1/me/preferences", "")
	if rec.Code != http.StatusOK || decode(t, rec)["handedness"] != "left" || prefs.gotOwner != aliceID {
		t.Fatalf("GET code=%d body=%s owner=%s", rec.Code, rec.Body, prefs.gotOwner)
	}
}

func TestPreferencesRejectsMalformedJSON(t *testing.T) {
	rec := do(newTestRouter(&stubUseCases{}), http.MethodPut, "/v1/me/preferences", `{bad`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("code=%d", rec.Code)
	}
}

func TestListRejectsBadQuery(t *testing.T) {
	for _, q := range []string{"limit=abc", "limit=1000", "cursor=%25%25"} {
		if rec := do(newTestRouter(&stubUseCases{}), http.MethodGet, "/v1/analyses?"+q, ""); rec.Code != http.StatusBadRequest {
			t.Errorf("%s → %d", q, rec.Code)
		}
	}
}

func TestAuthConfigIsPublic(t *testing.T) {
	rec := doWithAuth(newTestRouter(&stubUseCases{}), http.MethodGet, "/auth/config", "", "")
	body := decode(t, rec)
	if rec.Code != http.StatusOK || body["client_id"] != "tennis-web" || body["enabled"] != true {
		t.Fatalf("respuesta inesperada: %d %v", rec.Code, body)
	}
}
