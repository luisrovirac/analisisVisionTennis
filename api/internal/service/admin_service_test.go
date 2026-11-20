package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"tennisvision/api/internal/domain"
)

type fakeAdminRepo struct {
	users       map[string]domain.User
	invitations []domain.Invitation
}

func (r *fakeAdminRepo) ListUsers(context.Context, string, int) ([]domain.User, error) {
	return nil, nil
}

func (r *fakeAdminRepo) User(_ context.Context, id string) (domain.User, error) {
	u, ok := r.users[id]
	if !ok {
		return domain.User{}, domain.ErrResourceNotFound
	}
	return u, nil
}

func (r *fakeAdminRepo) FindVerifiedByEmail(_ context.Context, email string) (domain.User, bool, error) {
	for _, u := range r.users {
		if u.Email == email && u.EmailVerified {
			return u, true, nil
		}
	}
	return domain.User{}, false, nil
}

func (r *fakeAdminRepo) SetRoles(_ context.Context, _, id string, roles []domain.Role) error {
	u := r.users[id]
	u.Roles = roles
	r.users[id] = u
	return nil
}

func (r *fakeAdminRepo) GrantRoles(_ context.Context, _ *string, id string, roles []domain.Role, _ string) error {
	u := r.users[id]
	u.Roles = mergeRoles(u.Roles, roles...)
	r.users[id] = u
	return nil
}

func (r *fakeAdminRepo) SetStatus(_ context.Context, _, id string, s domain.UserStatus) error {
	u := r.users[id]
	u.Status = s
	r.users[id] = u
	return nil
}

func (r *fakeAdminRepo) CreateInvitation(_ context.Context, inv domain.Invitation) (domain.Invitation, error) {
	inv.ID = "inv-1"
	r.invitations = append(r.invitations, inv)
	return inv, nil
}

func (r *fakeAdminRepo) Invitations(context.Context, int) ([]domain.Invitation, error) {
	return r.invitations, nil
}
func (r *fakeAdminRepo) RevokeInvitation(context.Context, string, string) error  { return nil }
func (r *fakeAdminRepo) Audit(context.Context, int) ([]domain.AuditEntry, error) { return nil, nil }

const (
	adminID = "aaaaaaaa-0000-4000-8000-0000000000ad"
	coachID = "cccccccc-0000-4000-8000-0000000000cc"
)

func newAdminSvc() (*AdminService, *fakeAdminRepo, domain.User) {
	admin := domain.User{ID: adminID, Roles: []domain.Role{domain.RoleAdmin}, Status: domain.UserActive}
	repo := &fakeAdminRepo{users: map[string]domain.User{
		adminID: admin,
		coachID: {ID: coachID, Email: "coach@x.test", EmailVerified: true, Status: domain.UserActive},
	}}
	now := func() time.Time { return time.Date(2026, 11, 19, 0, 0, 0, 0, time.UTC) }
	return NewAdmin(repo, now), repo, admin
}

func TestAdminCannotLockThemselvesOut(t *testing.T) {
	svc, _, admin := newAdminSvc()
	if _, err := svc.SetRoles(context.Background(), admin, adminID, []string{"expert"}); !errors.Is(err, domain.ErrForbidden) {
		t.Fatalf("quitarse admin: %v", err)
	}
	if _, err := svc.SetStatus(context.Background(), admin, adminID, domain.UserSuspended); !errors.Is(err, domain.ErrForbidden) {
		t.Fatalf("suspenderse: %v", err)
	}
}

func TestInviteExistingVerifiedUserAppliesRolesNow(t *testing.T) {
	svc, repo, admin := newAdminSvc()
	res, err := svc.Invite(context.Background(), admin, "Coach@X.test", []string{"expert", "validator"})
	if err != nil || res.AppliedUser == nil || len(repo.invitations) != 0 {
		t.Fatalf("res=%+v err=%v", res, err)
	}
	if !repo.users[coachID].Has(domain.RoleExpert) || !repo.users[coachID].Has(domain.RoleValidator) {
		t.Fatal("roles no aplicados")
	}
}

func TestInviteNewEmailCreatesExpiringInvitation(t *testing.T) {
	svc, repo, admin := newAdminSvc()
	res, err := svc.Invite(context.Background(), admin, "nueva@x.test", []string{"validator"})
	if err != nil || res.Invitation == nil || repo.invitations[0].ExpiresAt.Sub(time.Date(2026, 11, 19, 0, 0, 0, 0, time.UTC)) != domain.InvitationTTL {
		t.Fatalf("res=%+v err=%v", res, err)
	}
	if _, err := svc.Invite(context.Background(), admin, "x@x.test", nil); !errors.Is(err, domain.ErrInvalidInput) {
		t.Fatal("una invitación sin roles no tiene sentido")
	}
}
