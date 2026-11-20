package service

import (
	"context"
	"slices"
	"testing"

	"tennisvision/api/internal/domain"
)

type fakeIdentityRepo struct {
	user        domain.User
	found       bool
	pending     bool
	invitations map[string][]domain.Role // email → roles
	grants      []domain.Role
	created     bool
}

func (r *fakeIdentityRepo) FindByIdentity(context.Context, domain.Principal) (domain.User, bool, bool, error) {
	return r.user, r.pending, r.found, nil
}

func (r *fakeIdentityRepo) CreateUser(_ context.Context, p domain.Principal) (domain.User, error) {
	r.created = true
	r.user = domain.User{ID: "u1", Email: p.Email, EmailVerified: p.EmailVerified, Status: domain.UserActive}
	return r.user, nil
}

func (r *fakeIdentityRepo) UpdateEmail(_ context.Context, _, email string, verified bool) error {
	r.user.Email, r.user.EmailVerified = email, verified
	return nil
}

func (r *fakeIdentityRepo) ClaimInvitations(_ context.Context, _, email string) ([]domain.Role, error) {
	roles := r.invitations[email]
	delete(r.invitations, email)
	return roles, nil
}

func (r *fakeIdentityRepo) GrantRoles(_ context.Context, _ *string, _ string, roles []domain.Role, _ string) error {
	r.grants = append(r.grants, roles...)
	return nil
}

func TestInvitationIsClaimedOnFirstLoginWithVerifiedEmail(t *testing.T) {
	repo := &fakeIdentityRepo{invitations: map[string][]domain.Role{"ana@x.test": {domain.RoleExpert}}}
	u, err := NewIdentity(repo, nil).Resolve(context.Background(),
		domain.Principal{Issuer: "i", Subject: "s", Email: "ana@x.test", EmailVerified: true})
	if err != nil || !repo.created || !slices.Equal(u.Roles, []domain.Role{domain.RoleExpert}) {
		t.Fatalf("u=%+v err=%v", u, err)
	}
}

// Sin email verificado nadie hereda roles: si no, bastaría registrarse con el email de otra persona.
func TestUnverifiedEmailNeverReceivesRoles(t *testing.T) {
	repo := &fakeIdentityRepo{invitations: map[string][]domain.Role{"ana@x.test": {domain.RoleValidator}}}
	svc := NewIdentity(repo, []string{"ana@x.test"})
	u, err := svc.Resolve(context.Background(), domain.Principal{Issuer: "i", Subject: "s", Email: "ana@x.test"})
	if err != nil || len(u.Roles) != 0 || len(repo.grants) != 0 || len(repo.invitations) != 1 {
		t.Fatalf("u=%+v grants=%v err=%v", u, repo.grants, err)
	}
}

func TestBootstrapAdminIsCaseInsensitiveAndIdempotent(t *testing.T) {
	repo := &fakeIdentityRepo{found: true, user: domain.User{ID: "u1", Email: "root@x.test", EmailVerified: true}}
	svc := NewIdentity(repo, []string{" ROOT@x.test "})
	p := domain.Principal{Issuer: "i", Subject: "s", Email: "root@x.test", EmailVerified: true}
	u, _ := svc.Resolve(context.Background(), p)
	if !u.Has(domain.RoleAdmin) || len(repo.grants) != 1 {
		t.Fatalf("u=%+v grants=%v", u, repo.grants)
	}
	repo.user = u
	if _, err := svc.Resolve(context.Background(), p); err != nil || len(repo.grants) != 1 {
		t.Fatal("un admin ya concedido no se vuelve a conceder")
	}
}

func TestKnownUserWithoutPendingDoesNotWrite(t *testing.T) {
	repo := &fakeIdentityRepo{found: true, user: domain.User{ID: "u1", Email: "a@x.test", EmailVerified: true,
		Roles: []domain.Role{domain.RoleExpert}}}
	u, err := NewIdentity(repo, nil).Resolve(context.Background(),
		domain.Principal{Email: "a@x.test", EmailVerified: true})
	if err != nil || repo.created || len(repo.grants) != 0 || !u.Has(domain.RoleExpert) {
		t.Fatalf("u=%+v err=%v", u, err)
	}
}
