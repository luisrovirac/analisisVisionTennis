package service

import (
	"context"
	"fmt"

	"tennisvision/api/internal/domain"
)

type PreferencesRepository interface {
	GetPreferences(ctx context.Context, userID string) (domain.Preferences, error)
	SavePreferences(ctx context.Context, userID string, p domain.Preferences) error
}

// PreferencesService lee y guarda las preferencias del usuario autenticado (userID = users.id).
type PreferencesService struct {
	repo PreferencesRepository
}

func NewPreferences(repo PreferencesRepository) *PreferencesService {
	return &PreferencesService{repo: repo}
}

func (s *PreferencesService) Get(ctx context.Context, userID string) (domain.Preferences, error) {
	p, err := s.repo.GetPreferences(ctx, userID)
	if err != nil {
		return domain.Preferences{}, fmt.Errorf("leer preferencias: %w", err)
	}
	return p, nil
}

// Update reemplaza las preferencias completas: repetir la misma petición deja el mismo estado.
func (s *PreferencesService) Update(ctx context.Context, userID string, p domain.Preferences) (domain.Preferences, error) {
	if err := p.Validate(); err != nil {
		return domain.Preferences{}, err
	}
	if err := s.repo.SavePreferences(ctx, userID, p); err != nil {
		return domain.Preferences{}, fmt.Errorf("guardar preferencias: %w", err)
	}
	return p, nil
}
