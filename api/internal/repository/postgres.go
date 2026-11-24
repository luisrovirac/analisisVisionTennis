// Package repository persiste los análisis y los usuarios en PostgreSQL.
package repository

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"tennisvision/api/internal/domain"
)

type PostgresRepository struct {
	pool *pgxpool.Pool
}

func NewPostgres(pool *pgxpool.Pool) *PostgresRepository {
	return &PostgresRepository{pool: pool}
}

const insertSQL = `
INSERT INTO analyses (id, user_id, stroke_type, handedness, camera_view, status, video_key, created_at, updated_at,
                      expert_review_allowed)
VALUES ($1::uuid, $2::uuid, NULLIF($3, ''), $4, $5, $6, $7, $8, $9, $10)`

func (r *PostgresRepository) Create(ctx context.Context, a domain.Analysis) error {
	_, err := r.pool.Exec(ctx, insertSQL, a.ID, a.UserID, a.Params.StrokeType, a.Params.Handedness,
		a.Params.CameraView, a.Status, a.VideoKey, a.CreatedAt, a.UpdatedAt, a.Params.ExpertReviewAllowed)
	if err != nil {
		return fmt.Errorf("insertar análisis: %w", err)
	}
	return nil
}

const selectColumns = `
SELECT id::text, user_id::text, COALESCE(stroke_type, ''), handedness, camera_view, status, video_key, attempts,
       error_code, error_message, report, kb_version, created_at, updated_at, expert_review_allowed
FROM analyses`

// El filtro por dueño va en el SQL (no solo en el servicio): defensa en profundidad.
const getSQL = selectColumns + ` WHERE id = $1::uuid AND user_id = $2::uuid`

func (r *PostgresRepository) Get(ctx context.Context, ownerID, id string) (domain.Analysis, error) {
	a, err := scanAnalysis(r.pool.QueryRow(ctx, getSQL, id, ownerID))
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.Analysis{}, domain.ErrNotFound
	}
	if err != nil {
		return domain.Analysis{}, fmt.Errorf("consultar análisis: %w", err)
	}
	return a, nil
}

// La comparación de filas (created_at, id) < (cursor) aprovecha el índice analyses_user_created_idx.
const listSQL = selectColumns + `
WHERE user_id = $1::uuid
  AND ($2::timestamptz IS NULL OR (created_at, id) < ($2::timestamptz, $3::uuid))
ORDER BY created_at DESC, id DESC
LIMIT $4`

func (r *PostgresRepository) List(ctx context.Context, ownerID string, after *domain.Cursor, limit int) ([]domain.Analysis, error) {
	var afterAt, afterID any
	if after != nil {
		afterAt, afterID = after.CreatedAt, after.ID
	}
	rows, err := r.pool.Query(ctx, listSQL, ownerID, afterAt, afterID, limit)
	if err != nil {
		return nil, fmt.Errorf("listar análisis: %w", err)
	}
	items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.Analysis, error) {
		return scanAnalysis(row)
	})
	if err != nil {
		return nil, fmt.Errorf("leer análisis: %w", err)
	}
	return items, nil
}

// --- Casos para el portal de expertos ---

// Solo análisis completados con el checkbox de revisión experta marcado: si el dueño lo desmarca,
// el caso desaparece de inmediato (también del detalle). El filtro por regla usa contención JSONB.
const casesSQL = selectColumns + `
WHERE status = 'completed' AND expert_review_allowed AND report IS NOT NULL
  AND ($1::text = '' OR COALESCE(report->>'stroke_type', stroke_type) = $1::text)
  AND ($2::text = ''
       OR report->'findings' @> jsonb_build_array(jsonb_build_object('rule_id', $2::text))
       OR report->'strengths' @> jsonb_build_array(jsonb_build_object('rule_id', $2::text)))
  AND ($3::timestamptz IS NULL OR (created_at, id) < ($3::timestamptz, $4::uuid))
ORDER BY created_at DESC, id DESC
LIMIT $5`

func (r *PostgresRepository) Cases(ctx context.Context, f domain.CaseFilter, limit int) ([]domain.Analysis, error) {
	var afterAt, afterID any
	if f.Query.After != nil {
		afterAt, afterID = f.Query.After.CreatedAt, f.Query.After.ID
	}
	rows, err := r.pool.Query(ctx, casesSQL, string(f.Stroke), f.RuleID, afterAt, afterID, limit)
	if err != nil {
		return nil, fmt.Errorf("listar casos: %w", err)
	}
	items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.Analysis, error) {
		return scanAnalysis(row)
	})
	if err != nil {
		return nil, fmt.Errorf("leer casos: %w", err)
	}
	return items, nil
}

const caseSQL = selectColumns + `
WHERE id = $1::uuid AND status = 'completed' AND expert_review_allowed AND report IS NOT NULL`

func (r *PostgresRepository) Case(ctx context.Context, id string) (domain.Analysis, error) {
	a, err := scanAnalysis(r.pool.QueryRow(ctx, caseSQL, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.Analysis{}, domain.ErrNotFound
	}
	if err != nil {
		return domain.Analysis{}, fmt.Errorf("consultar caso: %w", err)
	}
	return a, nil
}

func scanAnalysis(row pgx.Row) (domain.Analysis, error) {
	var a domain.Analysis
	err := row.Scan(
		&a.ID, &a.UserID, &a.Params.StrokeType, &a.Params.Handedness, &a.Params.CameraView,
		&a.Status, &a.VideoKey, &a.Attempts, &a.ErrorCode, &a.ErrorMessage, &a.Report,
		&a.KBVersion, &a.CreatedAt, &a.UpdatedAt, &a.Params.ExpertReviewAllowed)
	return a, err
}

const transitionSQL = `
UPDATE analyses SET status = $3, updated_at = now()
WHERE id = $1::uuid AND status = $2`

// Transition es una actualización condicional atómica: evita dobles encolados concurrentes.
func (r *PostgresRepository) Transition(ctx context.Context, id string, from, to domain.Status) (bool, error) {
	tag, err := r.pool.Exec(ctx, transitionSQL, id, from, to)
	if err != nil {
		return false, fmt.Errorf("cambiar estado: %w", err)
	}
	return tag.RowsAffected() == 1, nil
}

// La condición de estado va en el mismo DELETE para que no compita con el worker.
const deleteSQL = `
DELETE FROM analyses
WHERE id = $1::uuid AND user_id = $2::uuid AND status NOT IN ('queued', 'processing')`

func (r *PostgresRepository) Delete(ctx context.Context, ownerID, id string) (bool, error) {
	tag, err := r.pool.Exec(ctx, deleteSQL, id, ownerID)
	if err != nil {
		return false, fmt.Errorf("borrar análisis: %w", err)
	}
	return tag.RowsAffected() == 1, nil
}

const setExpertReviewSQL = `
UPDATE analyses SET expert_review_allowed = $3, updated_at = now() WHERE id = $1::uuid AND user_id = $2::uuid`

// SetExpertReview cambia el permiso de revisión experta; devuelve false si el análisis no es de ownerID.
func (r *PostgresRepository) SetExpertReview(ctx context.Context, ownerID, id string, allowed bool) (bool, error) {
	tag, err := r.pool.Exec(ctx, setExpertReviewSQL, id, ownerID, allowed)
	if err != nil {
		return false, fmt.Errorf("cambiar permiso de revisión: %w", err)
	}
	return tag.RowsAffected() == 1, nil
}

func (r *PostgresRepository) Ping(ctx context.Context) error {
	return r.pool.Ping(ctx)
}
