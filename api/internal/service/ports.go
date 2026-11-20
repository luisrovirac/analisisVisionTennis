package service

import (
	"context"
	"time"

	"tennisvision/api/internal/domain"
)

// Interfaces definidas por el consumidor (el servicio): pequeñas y específicas (ISP, DIP).

type AnalysisRepository interface {
	Create(ctx context.Context, a domain.Analysis) error
	// Get solo devuelve el análisis si pertenece a ownerID; si no, domain.ErrNotFound.
	Get(ctx context.Context, ownerID, id string) (domain.Analysis, error)
	// List devuelve hasta limit análisis de ownerID posteriores al cursor, del más reciente al más antiguo.
	List(ctx context.Context, ownerID string, after *domain.Cursor, limit int) ([]domain.Analysis, error)
	// Transition cambia el estado solo si el actual es `from`; devuelve false si otro proceso ganó.
	Transition(ctx context.Context, id string, from, to domain.Status) (bool, error)
	// Delete borra el análisis de ownerID solo si no está en cola ni procesándose;
	// devuelve false si no se borró (no existe, es ajeno o el worker lo tomó entretanto).
	Delete(ctx context.Context, ownerID, id string) (bool, error)
	// SetExpertReview cambia el checkbox de revisión experta; false si no existe o es ajeno.
	SetExpertReview(ctx context.Context, ownerID, id string, allowed bool) (bool, error)
}

type ObjectStore interface {
	PresignPut(ctx context.Context, key string, ttl time.Duration) (string, error)
	Stat(ctx context.Context, key string) (domain.ObjectInfo, error)
	// Remove borra los objetos indicados; los que no existen no son un error (idempotente).
	Remove(ctx context.Context, keys ...string) error
}

type JobPublisher interface {
	Publish(ctx context.Context, a domain.Analysis) error
}
