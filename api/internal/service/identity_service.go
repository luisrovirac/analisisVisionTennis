package service

import (
	"context"
	"fmt"
	"slices"
	"strings"

	"tennisvision/api/internal/domain"
)

type IdentityRepository interface {
	// FindByIdentity devuelve el usuario (con roles), si tiene una invitación pendiente para el email
	// del token y si existe.
	FindByIdentity(ctx context.Context, p domain.Principal) (user domain.User, pendingInvitation, found bool, err error)
	CreateUser(ctx context.Context, p domain.Principal) (domain.User, error)
	UpdateEmail(ctx context.Context, userID, email string, verified bool) error
	ClaimInvitations(ctx context.Context, userID, email string) ([]domain.Role, error)
	GrantRoles(ctx context.Context, actorID *string, userID string, roles []domain.Role, reason string) error
}

// IdentityService traduce el token al usuario interno y le aplica, al iniciar sesión, las invitaciones
// pendientes y el bootstrap del primer admin. Ambas cosas exigen un email VERIFICADO por el IdP: si no,
// cualquiera podría registrarse con el email de otra persona y heredar sus roles.
type IdentityService struct {
	repo            IdentityRepository
	bootstrapAdmins map[string]bool
}

func NewIdentity(repo IdentityRepository, bootstrapAdminEmails []string) *IdentityService {
	admins := map[string]bool{}
	for _, e := range bootstrapAdminEmails {
		if e = strings.ToLower(strings.TrimSpace(e)); e != "" {
			admins[e] = true
		}
	}
	return &IdentityService{repo: repo, bootstrapAdmins: admins}
}

// Resolve: en el caso habitual (usuario conocido, nada pendiente) es una sola lectura.
func (s *IdentityService) Resolve(ctx context.Context, p domain.Principal) (domain.User, error) {
	user, pending, found, err := s.repo.FindByIdentity(ctx, p)
	if err != nil {
		return domain.User{}, err
	}
	if !found {
		if user, err = s.repo.CreateUser(ctx, p); err != nil {
			return domain.User{}, err
		}
		pending = true // barato y cubre la invitación hecha antes del primer login
	} else if p.Email != "" && (p.Email != user.Email || p.EmailVerified != user.EmailVerified) {
		if err := s.repo.UpdateEmail(ctx, user.ID, p.Email, p.EmailVerified); err != nil {
			return domain.User{}, err
		}
		user.Email, user.EmailVerified = p.Email, p.EmailVerified
	}
	if !p.EmailVerified || p.Email == "" {
		return user, nil
	}
	if s.bootstrapAdmins[strings.ToLower(p.Email)] && !user.Has(domain.RoleAdmin) {
		if err := s.repo.GrantRoles(ctx, nil, user.ID, []domain.Role{domain.RoleAdmin}, "BOOTSTRAP_ADMIN_EMAILS"); err != nil {
			return domain.User{}, fmt.Errorf("bootstrap admin: %w", err)
		}
		user.Roles = mergeRoles(user.Roles, domain.RoleAdmin)
	}
	if pending {
		granted, err := s.repo.ClaimInvitations(ctx, user.ID, p.Email)
		if err != nil {
			return domain.User{}, err
		}
		user.Roles = mergeRoles(user.Roles, granted...)
	}
	return user, nil
}

func mergeRoles(current []domain.Role, extra ...domain.Role) []domain.Role {
	out := slices.Clone(current)
	for _, r := range extra {
		if !slices.Contains(out, r) {
			out = append(out, r)
		}
	}
	slices.Sort(out)
	return out
}
