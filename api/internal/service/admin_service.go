package service

import (
	"context"
	"fmt"
	"slices"
	"time"

	"tennisvision/api/internal/domain"
)

type AdminRepository interface {
	ListUsers(ctx context.Context, query string, limit int) ([]domain.User, error)
	User(ctx context.Context, id string) (domain.User, error)
	FindVerifiedByEmail(ctx context.Context, email string) (domain.User, bool, error)
	SetRoles(ctx context.Context, actorID, userID string, roles []domain.Role) error
	GrantRoles(ctx context.Context, actorID *string, userID string, roles []domain.Role, reason string) error
	SetStatus(ctx context.Context, actorID, userID string, status domain.UserStatus) error
	CreateInvitation(ctx context.Context, inv domain.Invitation) (domain.Invitation, error)
	Invitations(ctx context.Context, limit int) ([]domain.Invitation, error)
	RevokeInvitation(ctx context.Context, actorID, id string) error
	Audit(ctx context.Context, limit int) ([]domain.AuditEntry, error)
}

type ProfileRepository interface {
	Profile(ctx context.Context, userID string) (domain.Profile, bool, error)
	SaveProfile(ctx context.Context, userID string, p domain.Profile) error
}

const adminListLimit = 200

// AdminService: el admin crea y administra a los profesionales (expertos y validadores).
type AdminService struct {
	repo AdminRepository
	now  func() time.Time
}

func NewAdmin(repo AdminRepository, now func() time.Time) *AdminService {
	return &AdminService{repo: repo, now: now}
}

func (s *AdminService) ListUsers(ctx context.Context, query string) ([]domain.User, error) {
	return s.repo.ListUsers(ctx, query, adminListLimit)
}

// SetRoles reemplaza los roles de un usuario. Un admin no puede quitarse su propio rol de admin
// (evita quedarse sin nadie que administre por un clic equivocado).
func (s *AdminService) SetRoles(ctx context.Context, actor domain.User, userID string, raw []string) (domain.User, error) {
	roles, err := domain.ParseRoles(raw)
	if err != nil {
		return domain.User{}, err
	}
	if !domain.IsValidID(userID) {
		return domain.User{}, domain.ErrResourceNotFound
	}
	if userID == actor.ID && !slices.Contains(roles, domain.RoleAdmin) {
		return domain.User{}, fmt.Errorf("%w: no puedes quitarte tu propio rol de admin", domain.ErrForbidden)
	}
	if _, err := s.repo.User(ctx, userID); err != nil {
		return domain.User{}, err
	}
	if err := s.repo.SetRoles(ctx, actor.ID, userID, roles); err != nil {
		return domain.User{}, err
	}
	return s.repo.User(ctx, userID)
}

func (s *AdminService) SetStatus(ctx context.Context, actor domain.User, userID string, status domain.UserStatus) (domain.User, error) {
	if err := status.Validate(); err != nil {
		return domain.User{}, err
	}
	if !domain.IsValidID(userID) {
		return domain.User{}, domain.ErrResourceNotFound
	}
	if userID == actor.ID {
		return domain.User{}, fmt.Errorf("%w: no puedes cambiar el estado de tu propia cuenta", domain.ErrForbidden)
	}
	if err := s.repo.SetStatus(ctx, actor.ID, userID, status); err != nil {
		return domain.User{}, err
	}
	return s.repo.User(ctx, userID)
}

// InviteResult: si la persona ya tenía cuenta con ese email verificado, los roles se aplican al momento.
type InviteResult struct {
	Invitation  *domain.Invitation
	AppliedUser *domain.User
}

func (s *AdminService) Invite(ctx context.Context, actor domain.User, rawEmail string, rawRoles []string) (InviteResult, error) {
	email, err := domain.NormalizeEmail(rawEmail)
	if err != nil {
		return InviteResult{}, err
	}
	roles, err := domain.ParseRoles(rawRoles)
	if err != nil {
		return InviteResult{}, err
	}
	if len(roles) == 0 {
		return InviteResult{}, fmt.Errorf("%w: indica al menos un rol", domain.ErrInvalidInput)
	}
	existing, found, err := s.repo.FindVerifiedByEmail(ctx, email)
	if err != nil {
		return InviteResult{}, err
	}
	if found {
		if err := s.repo.GrantRoles(ctx, &actor.ID, existing.ID, roles, "invitación a usuario existente"); err != nil {
			return InviteResult{}, err
		}
		user, err := s.repo.User(ctx, existing.ID)
		return InviteResult{AppliedUser: &user}, err
	}
	inv, err := s.repo.CreateInvitation(ctx, domain.Invitation{
		Email: email, Roles: roles, InvitedBy: actor.ID, ExpiresAt: s.now().Add(domain.InvitationTTL),
	})
	return InviteResult{Invitation: &inv}, err
}

func (s *AdminService) Invitations(ctx context.Context) ([]domain.Invitation, error) {
	return s.repo.Invitations(ctx, adminListLimit)
}

func (s *AdminService) RevokeInvitation(ctx context.Context, actor domain.User, id string) error {
	if !domain.IsValidID(id) {
		return domain.ErrResourceNotFound
	}
	return s.repo.RevokeInvitation(ctx, actor.ID, id)
}

func (s *AdminService) Audit(ctx context.Context) ([]domain.AuditEntry, error) {
	return s.repo.Audit(ctx, adminListLimit)
}

// ProfileService: cada experto o validador mantiene su perfil profesional.
type ProfileService struct {
	repo ProfileRepository
}

func NewProfiles(repo ProfileRepository) *ProfileService {
	return &ProfileService{repo: repo}
}

func (s *ProfileService) Get(ctx context.Context, userID string) (*domain.Profile, error) {
	p, found, err := s.repo.Profile(ctx, userID)
	if err != nil || !found {
		return nil, err
	}
	return &p, nil
}

func (s *ProfileService) Save(ctx context.Context, user domain.User, p domain.Profile) (domain.Profile, error) {
	if !user.Has(domain.RoleExpert, domain.RoleValidator) {
		return domain.Profile{}, fmt.Errorf("%w: el perfil profesional es para expertos y validadores", domain.ErrForbidden)
	}
	if err := p.Validate(); err != nil {
		return domain.Profile{}, err
	}
	if err := s.repo.SaveProfile(ctx, user.ID, p); err != nil {
		return domain.Profile{}, err
	}
	p.UpdatedAt = time.Now().UTC()
	return p, nil
}
