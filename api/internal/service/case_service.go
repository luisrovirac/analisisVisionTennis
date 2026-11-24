package service

import (
	"context"
	"fmt"
	"time"

	"tennisvision/api/internal/domain"
)

// CaseRepository lee los análisis que los dueños permitieron revisar a los expertos.
type CaseRepository interface {
	Cases(ctx context.Context, f domain.CaseFilter, limit int) ([]domain.Analysis, error)
	// Case devuelve domain.ErrNotFound si no existe, no está completado o el dueño retiró el permiso.
	Case(ctx context.Context, id string) (domain.Analysis, error)
}

// CaseDetail es un caso con las URLs firmadas para verlo con el esqueleto superpuesto.
type CaseDetail struct {
	Analysis domain.Analysis
	Media    domain.MediaLinks
}

// CaseService alimenta la pestaña "Casos" del portal: el equipo profesional ve el video, el
// esqueleto y los hallazgos para comprobar si las reglas aciertan. Nunca expone al dueño.
type CaseService struct {
	repo  CaseRepository
	store MediaSigner
	ttl   time.Duration
	clock func() time.Time
}

func NewCases(repo CaseRepository, store MediaSigner, ttl time.Duration, clock func() time.Time) *CaseService {
	return &CaseService{repo: repo, store: store, ttl: ttl, clock: clock}
}

func (s *CaseService) List(ctx context.Context, f domain.CaseFilter) (Page, error) {
	items, err := s.repo.Cases(ctx, f, f.Query.Limit+1)
	if err != nil {
		return Page{}, fmt.Errorf("listar casos: %w", err)
	}
	if len(items) <= f.Query.Limit {
		return Page{Items: items}, nil
	}
	items = items[:f.Query.Limit]
	return Page{Items: items, NextCursor: items[len(items)-1].Cursor().Encode()}, nil
}

func (s *CaseService) Get(ctx context.Context, id string) (CaseDetail, error) {
	if !domain.IsValidID(id) {
		return CaseDetail{}, domain.ErrNotFound
	}
	a, err := s.repo.Case(ctx, id)
	if err != nil {
		return CaseDetail{}, err
	}
	media, err := SignMedia(ctx, s.store, a, s.ttl, s.clock())
	if err != nil {
		return CaseDetail{}, err
	}
	return CaseDetail{Analysis: a, Media: media}, nil
}
