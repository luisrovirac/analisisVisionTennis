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
	// MediaURLTTL es la vigencia de las URLs de lectura (video + pose); 0 = UploadURLTTL.
	MediaURLTTL time.Duration
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

// Media firma URLs de lectura del video y de la secuencia de pose de un análisis propio terminado.
// Va aparte de Get porque Get se consulta en bucle mientras se procesa y no conviene firmar cada vez.
func (s *AnalysisService) Media(ctx context.Context, ownerID, id string) (domain.MediaLinks, error) {
	a, err := s.Get(ctx, ownerID, id)
	if err != nil {
		return domain.MediaLinks{}, err
	}
	return SignMedia(ctx, s.store, a, s.mediaTTL(), s.clock())
}

func (s *AnalysisService) mediaTTL() time.Duration {
	if s.cfg.MediaURLTTL > 0 {
		return s.cfg.MediaURLTTL
	}
	return s.cfg.UploadURLTTL
}

// MediaSigner es lo mínimo que hace falta para firmar los medios de un análisis.
type MediaSigner interface {
	PresignGet(ctx context.Context, key string, ttl time.Duration) (string, error)
}

// SignMedia es compartido por el jugador (sus análisis) y el portal de expertos (casos permitidos).
func SignMedia(ctx context.Context, store MediaSigner, a domain.Analysis, ttl time.Duration, now time.Time) (domain.MediaLinks, error) {
	if a.Status != domain.StatusCompleted {
		return domain.MediaLinks{}, domain.ErrNotReady
	}
	video, err := store.PresignGet(ctx, a.VideoKey, ttl)
	if err != nil {
		return domain.MediaLinks{}, fmt.Errorf("firmar URL del video: %w", err)
	}
	pose, err := store.PresignGet(ctx, a.PoseKey(), ttl)
	if err != nil {
		return domain.MediaLinks{}, fmt.Errorf("firmar URL de la pose: %w", err)
	}
	return domain.MediaLinks{VideoURL: video, PoseURL: pose, ExpiresAt: now.Add(ttl)}, nil
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

// Delete borra el análisis y sus archivos. Los objetos se borran antes que la fila: si algo
// falla a medias, el análisis sigue visible y el usuario puede reintentar (Remove es idempotente).
func (s *AnalysisService) Delete(ctx context.Context, ownerID, id string) error {
	a, err := s.Get(ctx, ownerID, id)
	if err != nil {
		return err
	}
	if !a.Deletable() {
		return domain.ErrAnalysisBusy
	}
	if err := s.store.Remove(ctx, a.ObjectKeys()...); err != nil {
		return fmt.Errorf("borrar archivos del análisis: %w", err)
	}
	deleted, err := s.repo.Delete(ctx, ownerID, id)
	if err != nil {
		return fmt.Errorf("borrar análisis: %w", err)
	}
	if !deleted { // el usuario lo envió a analizar entre la lectura y el borrado
		return domain.ErrAnalysisBusy
	}
	return nil
}

// SetExpertReview cambia el checkbox "Permitir que expertos vean este video" de un análisis propio.
func (s *AnalysisService) SetExpertReview(ctx context.Context, ownerID, id string, allowed bool) (domain.Analysis, error) {
	if !domain.IsValidID(id) {
		return domain.Analysis{}, domain.ErrNotFound
	}
	changed, err := s.repo.SetExpertReview(ctx, ownerID, id, allowed)
	if err != nil {
		return domain.Analysis{}, fmt.Errorf("cambiar permiso de revisión: %w", err)
	}
	if !changed {
		return domain.Analysis{}, domain.ErrNotFound
	}
	return s.Get(ctx, ownerID, id)
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
