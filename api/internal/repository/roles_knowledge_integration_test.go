//go:build integration

package repository

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

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"tennisvision/api/internal/domain"
	"tennisvision/api/internal/service"
)

func newPrincipal(verified bool) domain.Principal {
	return domain.Principal{Issuer: "https://idp.test", Subject: uuid.NewString(),
		Email: uuid.NewString()[:8] + "@test.example", EmailVerified: verified}
}

func TestIdentityInvitationsRolesAndAudit(t *testing.T) {
	pool := newTestPool(t)
	users := NewPostgresUsers(pool)
	ctx := context.Background()
	admin, err := users.CreateUser(ctx, newPrincipal(true))
	if err != nil {
		t.Fatal(err)
	}
	p := newPrincipal(true)
	inv, err := users.CreateInvitation(ctx, domain.Invitation{Email: p.Email, Roles: []domain.Role{domain.RoleExpert, domain.RoleValidator},
		InvitedBy: admin.ID, ExpiresAt: time.Now().Add(time.Hour)})
	if err != nil || inv.ID == "" {
		t.Fatalf("inv=%+v err=%v", inv, err)
	}
	// Antes del primer login: no existe; con el usuario creado, la invitación figura como pendiente.
	if _, _, found, _ := users.FindByIdentity(ctx, p); found {
		t.Fatal("todavía no debería existir")
	}
	u, err := users.CreateUser(ctx, p)
	if err != nil {
		t.Fatal(err)
	}
	if _, pending, found, _ := users.FindByIdentity(ctx, p); !found || !pending {
		t.Fatalf("pending=%v found=%v", pending, found)
	}
	granted, err := users.ClaimInvitations(ctx, u.ID, p.Email)
	if err != nil || len(granted) != 2 {
		t.Fatalf("granted=%v err=%v", granted, err)
	}
	got, pending, _, _ := users.FindByIdentity(ctx, p)
	if pending || !slices.Equal(got.Roles, []domain.Role{domain.RoleExpert, domain.RoleValidator}) {
		t.Fatalf("roles=%v pending=%v", got.Roles, pending)
	}
	if err := users.SetRoles(ctx, admin.ID, u.ID, []domain.Role{domain.RoleExpert}); err != nil {
		t.Fatal(err)
	}
	if err := users.SetStatus(ctx, admin.ID, u.ID, domain.UserSuspended); err != nil {
		t.Fatal(err)
	}
	got, _ = users.User(ctx, u.ID)
	if got.Status != domain.UserSuspended || !slices.Equal(got.Roles, []domain.Role{domain.RoleExpert}) {
		t.Fatalf("user=%+v", got)
	}
	if found, ok, _ := users.FindVerifiedByEmail(ctx, p.Email); !ok || found.ID != u.ID {
		t.Fatal("debe encontrarse por email verificado")
	}
	if err := users.SaveProfile(ctx, u.ID, domain.Profile{DisplayName: "Ana", Specialty: "biomechanics"}); err != nil {
		t.Fatal(err)
	}
	if prof, ok, err := users.Profile(ctx, u.ID); err != nil || !ok || prof.DisplayName != "Ana" {
		t.Fatalf("perfil: %+v %v", prof, err)
	}
	entries, err := users.Audit(ctx, 50)
	if err != nil || len(entries) < 4 {
		t.Fatalf("auditoría: %d %v", len(entries), err)
	}
	if err := users.RevokeInvitation(ctx, admin.ID, inv.ID); !errors.Is(err, domain.ErrResourceNotFound) {
		t.Fatalf("una invitación aceptada no se revoca: %v", err)
	}
}

// seedKnowledge reproduce lo que hace el worker al arrancar: catálogo + versión inicial activa.
func seedKnowledge(t *testing.T, pool *pgxpool.Pool) domain.Release {
	t.Helper()
	ctx := context.Background()
	read := func(name string) []byte {
		raw, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "fixtures", name))
		if err != nil {
			t.Fatal(err)
		}
		return raw
	}
	var catalog []domain.MetricInfo
	if err := json.Unmarshal(read("metric_catalog.example.json"), &catalog); err != nil {
		t.Fatal(err)
	}
	for _, m := range catalog {
		if _, err := pool.Exec(ctx, `INSERT INTO metric_catalog (stroke, metric_id, label, unit) VALUES ($1,$2,$3,$4)
ON CONFLICT DO NOTHING`, m.Stroke, m.ID, m.Label, m.Unit); err != nil {
			t.Fatal(err)
		}
	}
	// Cada test parte de su propia versión activa (borra las de forehand de tests anteriores).
	if _, err := pool.Exec(ctx, `UPDATE kb_releases SET active = false WHERE stroke = 'forehand'`); err != nil {
		t.Fatal(err)
	}
	version := fmt.Sprintf("0.%d.0", time.Now().UnixMicro()*10) // margen para las versiones que publique el test
	if _, err := pool.Exec(ctx, `INSERT INTO kb_releases (stroke, version, snapshot, active, status)
VALUES ('forehand', $1, jsonb_set($2::jsonb, '{version}', to_jsonb($1::text)), true, 'valid')`,
		version, string(read("knowledge_base.example.json"))); err != nil {
		t.Fatal(err)
	}
	active, err := NewPostgresKnowledge(pool).ActiveRelease(ctx, domain.StrokeForehand)
	if err != nil {
		t.Fatal(err)
	}
	return active
}

func TestKnowledgeProposalApprovalPublishesAtomically(t *testing.T) {
	pool := newTestPool(t)
	ctx := context.Background()
	users := NewPostgresUsers(pool)
	repo := NewPostgresKnowledge(pool)
	base := seedKnowledge(t, pool)
	expert, _ := users.CreateUser(ctx, newPrincipal(true))
	validator, _ := users.CreateUser(ctx, newPrincipal(true))
	svc := service.NewKnowledge(repo, service.KnowledgeConfig{MinMetricConfidence: 0.5}, time.Now)
	expertU := domain.User{ID: expert.ID, Roles: []domain.Role{domain.RoleExpert}}
	validatorU := domain.User{ID: validator.ID, Roles: []domain.Role{domain.RoleValidator}}

	rule := domain.Rule{ID: "FH-POST-001", Title: "Piernas rígidas", Category: "fundamental", Impact: 4, Difficulty: 1,
		When:        domain.ConditionGroup{All: []domain.Condition{{Metric: "knee_angle_min", Op: "gt", Value: domain.Threshold{Value: 165}}}},
		Explanation: "e", Correction: "c"}
	in := domain.ProposalInput{Stroke: domain.StrokeForehand, Title: "Umbral rodilla", Rationale: "falsos positivos",
		Changes: []domain.Change{{Op: domain.OpUpsertRule, Rule: &rule}}}
	p, err := svc.CreateProposal(ctx, expertU, in)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := svc.Submit(ctx, expertU, p.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.AddComment(ctx, validatorU, p.ID, "¿cuántos casos?"); err != nil {
		t.Fatal(err)
	}
	detail, err := svc.Proposal(ctx, p.ID)
	if err != nil || len(detail.Comments) != 1 || detail.Problems != "" || detail.Stale {
		t.Fatalf("detalle: %+v %v", detail, err)
	}
	// La base de datos también impide que el autor se decida a sí mismo.
	if _, err := pool.Exec(ctx, `UPDATE kb_proposals SET decided_by = author_id WHERE id = $1::uuid`, p.ID); err == nil {
		t.Fatal("la restricción de cuatro ojos debe rechazarlo")
	}
	rel, err := svc.Approve(ctx, validatorU, p.ID, "ok")
	if err != nil {
		t.Fatal(err)
	}
	next, _ := domain.NextVersion(base.Version)
	if rel.Version != next || !rel.Active {
		t.Fatalf("versión publicada: %+v", rel)
	}
	active, _ := repo.ActiveRelease(ctx, domain.StrokeForehand)
	i := slices.IndexFunc(active.KB.Rules, func(r domain.Rule) bool { return r.ID == "FH-POST-001" })
	if active.ID != rel.ID || !active.KB.Rules[i].Validated {
		t.Fatal("la versión aprobada debe ser la activa, con la regla validada")
	}
	approved, _ := repo.Proposal(ctx, p.ID)
	if approved.Status != domain.ProposalApproved || approved.ReleaseID == nil || *approved.ReleaseID != rel.ID {
		t.Fatalf("propuesta: %+v", approved)
	}
	// Rollback: la versión anterior vuelve a ser la activa (y solo ella).
	if _, err := svc.Activate(ctx, validatorU, base.ID); err != nil {
		t.Fatal(err)
	}
	var actives int
	_ = pool.QueryRow(ctx, `SELECT count(*) FROM kb_releases WHERE stroke = 'forehand' AND active`).Scan(&actives)
	if again, _ := repo.ActiveRelease(ctx, domain.StrokeForehand); again.ID != base.ID || actives != 1 {
		t.Fatalf("rollback: activa=%s n=%d", again.ID, actives)
	}
}

func TestStaleProposalIsRejectedInsideTheTransaction(t *testing.T) {
	pool := newTestPool(t)
	ctx := context.Background()
	users := NewPostgresUsers(pool)
	seedKnowledge(t, pool)
	expert, _ := users.CreateUser(ctx, newPrincipal(true))
	validator, _ := users.CreateUser(ctx, newPrincipal(true))
	svc := service.NewKnowledge(NewPostgresKnowledge(pool), service.KnowledgeConfig{MinMetricConfidence: 0.5}, time.Now)
	expertU, validatorU := domain.User{ID: expert.ID}, domain.User{ID: validator.ID}
	mk := func() domain.Proposal {
		in := domain.ProposalInput{Stroke: domain.StrokeForehand, Title: "t", Rationale: "r",
			Changes: []domain.Change{{Op: domain.OpRemoveRule, RuleID: "FH-SYNC-002"}}} // ninguna otra regla la cita
		p, err := svc.CreateProposal(ctx, expertU, in)
		if err == nil {
			p, err = svc.Submit(ctx, expertU, p.ID)
		}
		if err != nil {
			t.Fatal(err)
		}
		return p
	}
	first, second := mk(), mk()
	if _, err := svc.Approve(ctx, validatorU, first.ID, ""); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.Approve(ctx, validatorU, second.ID, ""); !errors.Is(err, domain.ErrConflict) {
		t.Fatalf("err=%v", err)
	}
}

func TestExpertReviewFlagAndPreviewSamples(t *testing.T) {
	pool := newTestPool(t)
	ctx := context.Background()
	repo := NewPostgres(pool)
	owner := newUser(t)
	params := domain.AnalysisParams{StrokeType: domain.StrokeForehand, Handedness: domain.HandRight, CameraView: domain.ViewSide,
		ExpertReviewAllowed: true}
	a := domain.NewAnalysis(uuid.NewString(), owner, params, time.Now().UTC())
	if err := repo.Create(ctx, a); err != nil {
		t.Fatal(err)
	}
	report := `{"stroke_type":"forehand","metrics":[{"id":"knee_angle_min","value":170,"confidence":0.9}]}`
	if _, err := pool.Exec(ctx, `UPDATE analyses SET status = 'completed', report = $2::jsonb WHERE id = $1::uuid`, a.ID, report); err != nil {
		t.Fatal(err)
	}
	contains := func() bool {
		samples, err := NewPostgresKnowledge(pool).RecentSamples(ctx, domain.StrokeForehand, 1000)
		if err != nil {
			t.Fatal(err)
		}
		return slices.ContainsFunc(samples, func(s domain.MetricSample) bool { return s["knee_angle_min"].Value == 170 })
	}
	if !contains() {
		t.Fatal("un análisis permitido debe usarse en la previsualización")
	}
	if ok, err := repo.SetExpertReview(ctx, newUser(t), a.ID, false); err != nil || ok {
		t.Fatal("otro usuario no puede cambiar el permiso")
	}
	if ok, err := repo.SetExpertReview(ctx, owner, a.ID, false); err != nil || !ok {
		t.Fatalf("ok=%v err=%v", ok, err)
	}
	got, _ := repo.Get(ctx, owner, a.ID)
	if got.Params.ExpertReviewAllowed {
		t.Fatal("el permiso debería estar desmarcado")
	}
	if contains() {
		t.Fatal("un análisis desmarcado sale de inmediato de lo que ven los expertos")
	}
}
