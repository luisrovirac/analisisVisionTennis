// Package service implementa los casos de uso de la API.
package service

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"tennisvision/api/internal/domain"
)

type Config struct {
	UploadURLTTL   time.Duration
	MaxUploadBytes int64
}

// Page es una página de un listado; NextCursor vacío indica que no hay más.
type Page struct {
	Items      []domain.Analysis
	NextCursor string
}

// UploadTicket es lo que recibe el cliente para subir el video directamente al almacenamiento.
type UploadTicket struct {
	Analysis  domain.Analysis
	UploadURL string
	ExpiresAt time.Time
}

type AnalysisService struct {
	repo  AnalysisRepository
	store ObjectStore
	jobs  JobPublisher
	clock func() time.Time
	newID func() string
	cfg   Config
}

func New(repo AnalysisRepository, store ObjectStore, jobs JobPublisher,
	clock func() time.Time, newID func() string, cfg Config) *AnalysisService {
	return &AnalysisService{repo: repo, store: store, jobs: jobs, clock: clock, newID: newID, cfg: cfg}
}

// Todos los casos de uso reciben ownerID (users.id del usuario autenticado): un usuario
// nunca ve ni modifica análisis ajenos, que para él simplemente no existen (ErrNotFound).

func (s *AnalysisService) Create(ctx context.Context, ownerID string, p domain.AnalysisParams) (UploadTicket, error) {
	p = p.WithDefaults()
	if err := p.Validate(); err != nil {
		return UploadTicket{}, err
	}
	now := s.clock()
	a := domain.NewAnalysis(s.newID(), ownerID, p, now)
	url, err := s.store.PresignPut(ctx, a.VideoKey, s.cfg.UploadURLTTL)
	if err != nil {
		return UploadTicket{}, fmt.Errorf("firmar URL de subida: %w", err)
	}
	if err := s.repo.Create(ctx, a); err != nil {
		return UploadTicket{}, fmt.Errorf("guardar análisis: %w", err)
	}
	return UploadTicket{Analysis: a, UploadURL: url, ExpiresAt: now.Add(s.cfg.UploadURLTTL)}, nil
}

// Submit encola el análisis una vez subido el video. Es idempotente: si ya salió de
// awaiting_upload devuelve el estado actual sin volver a encolar.
func (s *AnalysisService) Submit(ctx context.Context, ownerID, id string) (domain.Analysis, error) {
	a, err := s.Get(ctx, ownerID, id)
	if err != nil || a.Status != domain.StatusAwaitingUpload {
		return a, err
	}
	if err := s.verifyUpload(ctx, a.VideoKey); err != nil {
		return a, err
	}
	return s.enqueue(ctx, ownerID, a)
}

func (s *AnalysisService) Get(ctx context.Context, ownerID, id string) (domain.Analysis, error) {
	if !domain.IsValidID(id) {
		return domain.Analysis{}, domain.ErrNotFound
	}
	return s.repo.Get(ctx, ownerID, id)
}

// List pide un elemento de más para saber, sin otra consulta, si existe una página siguiente.
func (s *AnalysisService) List(ctx context.Context, ownerID string, q domain.ListQuery) (Page, error) {
	items, err := s.repo.List(ctx, ownerID, q.After, q.Limit+1)
	if err != nil {
		return Page{}, fmt.Errorf("listar análisis: %w", err)
	}
	if len(items) <= q.Limit {
		return Page{Items: items}, nil
	}
	items = items[:q.Limit]
	return Page{Items: items, NextCursor: items[len(items)-1].Cursor().Encode()}, nil
}

func (s *AnalysisService) verifyUpload(ctx context.Context, key string) error {
	info, err := s.store.Stat(ctx, key)
	switch {
	case errors.Is(err, domain.ErrObjectNotFound):
		return domain.ErrUploadMissing
	case err != nil:
		return fmt.Errorf("consultar video subido: %w", err)
	case info.Size > s.cfg.MaxUploadBytes:
		return domain.ErrUploadTooLarge
	case !isVideoContentType(info.ContentType):
		return domain.ErrUnsupportedMedia
	}
	return nil
}

// Algunos clientes suben sin content-type preciso; el worker valida el contenido real.
func isVideoContentType(ct string) bool {
	return strings.HasPrefix(ct, "video/") || ct == "application/octet-stream" || ct == ""
}

func (s *AnalysisService) enqueue(ctx context.Context, ownerID string, a domain.Analysis) (domain.Analysis, error) {
	moved, err := s.repo.Transition(ctx, a.ID, domain.StatusAwaitingUpload, domain.StatusQueued)
	if err != nil {
		return a, fmt.Errorf("marcar como encolado: %w", err)
	}
	if !moved {
		return s.repo.Get(ctx, ownerID, a.ID)
	}
	if err := s.jobs.Publish(ctx, a); err != nil {
		return a, errors.Join(fmt.Errorf("publicar trabajo: %w", err), s.rollback(ctx, a.ID))
	}
	a.Status = domain.StatusQueued
	return a, nil
}

// rollback devuelve el análisis a awaiting_upload para que el cliente pueda reintentar el submit.
func (s *AnalysisService) rollback(ctx context.Context, id string) error {
	_, err := s.repo.Transition(context.WithoutCancel(ctx), id, domain.StatusQueued, domain.StatusAwaitingUpload)
	if err != nil {
		return fmt.Errorf("revertir estado: %w", err)
	}
	return nil
}
