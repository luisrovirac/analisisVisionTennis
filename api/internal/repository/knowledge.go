package repository

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"tennisvision/api/internal/domain"
	"tennisvision/api/internal/service"
)

// PostgresKnowledge persiste propuestas, comentarios y versiones publicadas de la base de conocimiento.
type PostgresKnowledge struct {
	pool *pgxpool.Pool
}

func NewPostgresKnowledge(pool *pgxpool.Pool) *PostgresKnowledge {
	return &PostgresKnowledge{pool: pool}
}

// nameOf muestra el nombre profesional si existe; si no, el email. Solo se usa dentro del portal.
const nameOf = `COALESCE((SELECT display_name FROM professional_profiles pp WHERE pp.user_id = %s),
                (SELECT email FROM users uu WHERE uu.id = %s), '')`

func named(col string) string { return fmt.Sprintf(nameOf, col, col) }

func (k *PostgresKnowledge) Catalog(ctx context.Context, stroke domain.StrokeType) ([]domain.MetricInfo, error) {
	rows, err := k.pool.Query(ctx, `
SELECT stroke, metric_id, label, unit FROM metric_catalog
WHERE $1 = '' OR stroke = $1 ORDER BY stroke, metric_id`, string(stroke))
	if err != nil {
		return nil, fmt.Errorf("leer catálogo: %w", err)
	}
	items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.MetricInfo, error) {
		var m domain.MetricInfo
		return m, row.Scan(&m.Stroke, &m.ID, &m.Label, &m.Unit)
	})
	if err != nil {
		return nil, fmt.Errorf("leer catálogo: %w", err)
	}
	return items, nil
}

// --- Versiones ---

var releaseColumns = `
SELECT kb_releases.id::text, stroke, version, snapshot, changelog, changelog_entries, bump, proposal_id::text,
       proposed_by::text, ` + named("proposed_by") + `, validated_by::text, ` + named("validated_by") + `,
       active, status, invalid_reason, created_at
FROM kb_releases`

func scanRelease(row pgx.Row) (domain.Release, error) {
	var r domain.Release
	var snapshot, entries []byte
	var bump *string
	if err := row.Scan(&r.ID, &r.Stroke, &r.Version, &snapshot, &r.Changelog, &entries, &bump, &r.ProposalID,
		&r.ProposedBy, &r.ProposerName, &r.ValidatedBy, &r.ValidatorName, &r.Active, &r.Status, &r.InvalidReason,
		&r.CreatedAt); err != nil {
		return domain.Release{}, err
	}
	if err := json.Unmarshal(snapshot, &r.KB); err != nil {
		return domain.Release{}, fmt.Errorf("snapshot ilegible en %s: %w", r.ID, err)
	}
	if err := json.Unmarshal(entries, &r.Entries); err != nil {
		return domain.Release{}, fmt.Errorf("changelog ilegible en %s: %w", r.ID, err)
	}
	if bump != nil {
		b := domain.Bump(*bump)
		r.Bump = &b
	}
	return r, nil
}

func oneRelease(row pgx.Row) (domain.Release, error) {
	r, err := scanRelease(row)
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.Release{}, domain.ErrResourceNotFound
	}
	if err != nil {
		return domain.Release{}, fmt.Errorf("consultar versión: %w", err)
	}
	return r, nil
}

func (k *PostgresKnowledge) ActiveRelease(ctx context.Context, stroke domain.StrokeType) (domain.Release, error) {
	return oneRelease(k.pool.QueryRow(ctx, releaseColumns+` WHERE stroke = $1 AND active`, stroke))
}

func (k *PostgresKnowledge) Release(ctx context.Context, id string) (domain.Release, error) {
	return oneRelease(k.pool.QueryRow(ctx, releaseColumns+` WHERE kb_releases.id = $1::uuid`, id))
}

func (k *PostgresKnowledge) Releases(ctx context.Context, stroke domain.StrokeType, limit int) ([]domain.Release, error) {
	rows, err := k.pool.Query(ctx, releaseColumns+` WHERE stroke = $1 ORDER BY created_at DESC LIMIT $2`, stroke, limit)
	if err != nil {
		return nil, fmt.Errorf("listar versiones: %w", err)
	}
	items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.Release, error) { return scanRelease(row) })
	if err != nil {
		return nil, fmt.Errorf("leer versiones: %w", err)
	}
	return items, nil
}

// --- Propuestas ---

var proposalColumns = `
SELECT p.id::text, p.author_id::text, ` + named("p.author_id") + `, p.stroke, p.title, p.rationale, p.evidence, p.changes,
       p.status, p.base_release_id::text, p.decided_by::text, ` + named("p.decided_by") + `, p.decided_at,
       p.decision_note, p.release_id::text, p.created_at, p.updated_at
FROM kb_proposals p`

func scanProposal(row pgx.Row) (domain.Proposal, error) {
	var p domain.Proposal
	var changes []byte
	if err := row.Scan(&p.ID, &p.AuthorID, &p.AuthorName, &p.Stroke, &p.Title, &p.Rationale, &p.Evidence, &changes,
		&p.Status, &p.BaseReleaseID, &p.DecidedBy, &p.DeciderName, &p.DecidedAt, &p.DecisionNote, &p.ReleaseID,
		&p.CreatedAt, &p.UpdatedAt); err != nil {
		return domain.Proposal{}, err
	}
	if err := json.Unmarshal(changes, &p.Changes); err != nil {
		return domain.Proposal{}, fmt.Errorf("cambios ilegibles en %s: %w", p.ID, err)
	}
	return p, nil
}

func (k *PostgresKnowledge) CreateProposal(ctx context.Context, p domain.Proposal) (domain.Proposal, error) {
	changes, err := json.Marshal(p.Changes)
	if err != nil {
		return domain.Proposal{}, fmt.Errorf("serializar cambios: %w", err)
	}
	var id string
	if err := k.pool.QueryRow(ctx, `
INSERT INTO kb_proposals (author_id, stroke, title, rationale, evidence, changes, status)
VALUES ($1::uuid, $2, $3, $4, $5, $6, $7) RETURNING id::text`,
		p.AuthorID, p.Stroke, p.Title, p.Rationale, p.Evidence, changes, p.Status).Scan(&id); err != nil {
		return domain.Proposal{}, fmt.Errorf("crear propuesta: %w", err)
	}
	return k.Proposal(ctx, id)
}

func (k *PostgresKnowledge) Proposal(ctx context.Context, id string) (domain.Proposal, error) {
	p, err := scanProposal(k.pool.QueryRow(ctx, proposalColumns+` WHERE p.id = $1::uuid`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.Proposal{}, domain.ErrResourceNotFound
	}
	if err != nil {
		return domain.Proposal{}, fmt.Errorf("consultar propuesta: %w", err)
	}
	return p, nil
}

func (k *PostgresKnowledge) Proposals(ctx context.Context, f service.ProposalFilter) ([]domain.Proposal, error) {
	rows, err := k.pool.Query(ctx, proposalColumns+`
WHERE ($1 = '' OR p.status = $1) AND ($2 = '' OR p.author_id = NULLIF($2, '')::uuid)
ORDER BY p.updated_at DESC LIMIT $3`, string(f.Status), f.AuthorID, f.Limit)
	if err != nil {
		return nil, fmt.Errorf("listar propuestas: %w", err)
	}
	items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.Proposal, error) { return scanProposal(row) })
	if err != nil {
		return nil, fmt.Errorf("leer propuestas: %w", err)
	}
	return items, nil
}

func (k *PostgresKnowledge) UpdateProposal(ctx context.Context, p domain.Proposal, from ...domain.ProposalStatus) (bool, error) {
	changes, err := json.Marshal(p.Changes)
	if err != nil {
		return false, fmt.Errorf("serializar cambios: %w", err)
	}
	tag, err := k.pool.Exec(ctx, `
UPDATE kb_proposals SET title = $2, rationale = $3, evidence = $4, changes = $5, status = $6,
       base_release_id = $7::uuid, updated_at = now()
WHERE id = $1::uuid AND status = ANY($8)`,
		p.ID, p.Title, p.Rationale, p.Evidence, changes, p.Status, p.BaseReleaseID, statusStrings(from))
	if err != nil {
		return false, fmt.Errorf("guardar propuesta: %w", err)
	}
	return tag.RowsAffected() == 1, nil
}

func (k *PostgresKnowledge) Decide(ctx context.Context, id, deciderID string, to domain.ProposalStatus, note string) (bool, error) {
	tag, err := k.pool.Exec(ctx, `
UPDATE kb_proposals SET status = $3, decided_by = $2::uuid, decided_at = now(), decision_note = $4, updated_at = now()
WHERE id = $1::uuid AND status = 'submitted' AND author_id <> $2::uuid`, id, deciderID, to, note)
	if err != nil {
		return false, fmt.Errorf("decidir propuesta: %w", err)
	}
	return tag.RowsAffected() == 1, nil
}

// Approve: todo o nada. Bloquea la propuesta y la versión activa del golpe (FOR UPDATE) para que dos
// aprobaciones simultáneas no publiquen sobre la misma base, construye la base nueva y la publica.
func (k *PostgresKnowledge) Approve(ctx context.Context, id, validatorID, note string, build service.ApproveFunc) (domain.Release, error) {
	var published domain.Release
	err := pgx.BeginFunc(ctx, k.pool, func(tx pgx.Tx) error {
		p, err := scanProposal(tx.QueryRow(ctx, proposalColumns+` WHERE p.id = $1::uuid FOR UPDATE OF p`, id))
		if errors.Is(err, pgx.ErrNoRows) {
			return domain.ErrResourceNotFound
		}
		if err != nil {
			return fmt.Errorf("bloquear propuesta: %w", err)
		}
		if p.Status != domain.ProposalSubmitted {
			return fmt.Errorf("%w: la propuesta está %s", domain.ErrConflict, p.Status)
		}
		if p.AuthorID == validatorID {
			return fmt.Errorf("%w: nadie valida sus propias propuestas", domain.ErrForbidden)
		}
		active, err := oneRelease(tx.QueryRow(ctx, releaseColumns+` WHERE stroke = $1 AND active FOR UPDATE OF kb_releases`, p.Stroke))
		if err != nil {
			return err
		}
		catalog, err := catalogTx(ctx, tx, p.Stroke)
		if err != nil {
			return err
		}
		pub, err := build(active, p, catalog)
		if err != nil {
			return err
		}
		kb := pub.KB
		snapshot, err := json.Marshal(kb)
		if err != nil {
			return fmt.Errorf("serializar versión: %w", err)
		}
		entries, err := json.Marshal(pub.Entries)
		if err != nil {
			return fmt.Errorf("serializar changelog: %w", err)
		}
		if _, err := tx.Exec(ctx, `UPDATE kb_releases SET active = false WHERE id = $1::uuid`, active.ID); err != nil {
			return fmt.Errorf("desactivar versión anterior: %w", err)
		}
		var releaseID string
		if err := tx.QueryRow(ctx, `
INSERT INTO kb_releases (stroke, version, snapshot, changelog, changelog_entries, bump, proposal_id, proposed_by,
                         validated_by, active, status, activated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7::uuid, $8::uuid, $9::uuid, true, 'valid', now()) RETURNING id::text`,
			p.Stroke, kb.Version, snapshot, pub.Summary, entries, string(pub.Bump), p.ID, p.AuthorID, validatorID).Scan(&releaseID); err != nil {
			return fmt.Errorf("publicar versión: %w", err)
		}
		if _, err := tx.Exec(ctx, `
UPDATE kb_proposals SET status = 'approved', decided_by = $2::uuid, decided_at = now(), decision_note = $3,
       release_id = $4::uuid, updated_at = now()
WHERE id = $1::uuid`, p.ID, validatorID, note, releaseID); err != nil {
			return fmt.Errorf("cerrar propuesta: %w", err)
		}
		if err := audit(ctx, tx, &validatorID, "proposal.approved", "proposal:"+p.ID,
			map[string]any{"release_id": releaseID, "version": kb.Version, "stroke": p.Stroke, "bump": pub.Bump}); err != nil {
			return err
		}
		published, err = oneRelease(tx.QueryRow(ctx, releaseColumns+` WHERE kb_releases.id = $1::uuid`, releaseID))
		return err
	})
	return published, err
}

func catalogTx(ctx context.Context, tx pgx.Tx, stroke domain.StrokeType) ([]domain.MetricInfo, error) {
	rows, err := tx.Query(ctx, `SELECT stroke, metric_id, label, unit FROM metric_catalog WHERE stroke = $1`, stroke)
	if err != nil {
		return nil, fmt.Errorf("leer catálogo: %w", err)
	}
	return pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.MetricInfo, error) {
		var m domain.MetricInfo
		return m, row.Scan(&m.Stroke, &m.ID, &m.Label, &m.Unit)
	})
}

// Activate (rollback): la versión elegida pasa a ser la activa de su golpe.
func (k *PostgresKnowledge) Activate(ctx context.Context, releaseID, actorID string) (domain.Release, error) {
	var activated domain.Release
	err := pgx.BeginFunc(ctx, k.pool, func(tx pgx.Tx) error {
		target, err := oneRelease(tx.QueryRow(ctx, releaseColumns+` WHERE kb_releases.id = $1::uuid FOR UPDATE OF kb_releases`, releaseID))
		if err != nil {
			return err
		}
		if target.Status != domain.ReleaseValid {
			return fmt.Errorf("%w: esa versión fue rechazada por el worker y no se puede activar", domain.ErrConflict)
		}
		if target.Active {
			activated = target
			return nil
		}
		if _, err := tx.Exec(ctx, `UPDATE kb_releases SET active = false WHERE stroke = $1 AND active`, target.Stroke); err != nil {
			return fmt.Errorf("desactivar versión: %w", err)
		}
		if _, err := tx.Exec(ctx, `UPDATE kb_releases SET active = true, activated_at = now() WHERE id = $1::uuid`, target.ID); err != nil {
			return fmt.Errorf("activar versión: %w", err)
		}
		if err := audit(ctx, tx, &actorID, "release.activated", "release:"+target.ID,
			map[string]any{"stroke": target.Stroke, "version": target.Version}); err != nil {
			return err
		}
		target.Active = true
		activated = target
		return nil
	})
	return activated, err
}

// --- Comentarios ---

func (k *PostgresKnowledge) AddComment(ctx context.Context, c domain.Comment) (domain.Comment, error) {
	if err := k.pool.QueryRow(ctx, `
INSERT INTO kb_proposal_comments (proposal_id, author_id, body) VALUES ($1::uuid, $2::uuid, $3)
RETURNING id::text, created_at, `+named("$2::uuid"), c.ProposalID, c.AuthorID, c.Body).Scan(&c.ID, &c.CreatedAt, &c.AuthorName); err != nil {
		return domain.Comment{}, fmt.Errorf("comentar: %w", err)
	}
	return c, nil
}

func (k *PostgresKnowledge) Comments(ctx context.Context, proposalID string) ([]domain.Comment, error) {
	rows, err := k.pool.Query(ctx, `
SELECT c.id::text, c.proposal_id::text, c.author_id::text, `+named("c.author_id")+`, c.body, c.created_at
FROM kb_proposal_comments c WHERE c.proposal_id = $1::uuid ORDER BY c.created_at`, proposalID)
	if err != nil {
		return nil, fmt.Errorf("listar comentarios: %w", err)
	}
	items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.Comment, error) {
		var c domain.Comment
		return c, row.Scan(&c.ID, &c.ProposalID, &c.AuthorID, &c.AuthorName, &c.Body, &c.CreatedAt)
	})
	if err != nil {
		return nil, fmt.Errorf("leer comentarios: %w", err)
	}
	return items, nil
}

// --- Previsualización ---

// Solo análisis con el checkbox de revisión experta marcado: lo mismo que puede ver un experto.
const recentSamplesSQL = `
SELECT id::text, camera_view, report->'metrics' FROM analyses
WHERE status = 'completed' AND expert_review_allowed AND report IS NOT NULL
  AND COALESCE(report->>'stroke_type', stroke_type) = $1
ORDER BY created_at DESC LIMIT $2`

func (k *PostgresKnowledge) RecentSamples(ctx context.Context, stroke domain.StrokeType, limit int) ([]domain.Sample, error) {
	rows, err := k.pool.Query(ctx, recentSamplesSQL, stroke, limit)
	if err != nil {
		return nil, fmt.Errorf("leer análisis recientes: %w", err)
	}
	samples, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.Sample, error) {
		var s domain.Sample
		var raw []byte
		if err := row.Scan(&s.ID, &s.View, &raw); err != nil {
			return s, err
		}
		s.Metrics = parseMetrics(raw)
		return s, nil
	})
	if err != nil {
		return nil, fmt.Errorf("leer métricas: %w", err)
	}
	return samples, nil
}

// parseMetrics lee report.metrics; un reporte antiguo o raro da una muestra vacía (nada medible).
func parseMetrics(raw []byte) domain.MetricSample {
	var metrics []struct {
		ID         string  `json:"id"`
		Value      float64 `json:"value"`
		Confidence float64 `json:"confidence"`
	}
	s := domain.MetricSample{}
	if json.Unmarshal(raw, &metrics) != nil {
		return s
	}
	for _, m := range metrics {
		s[m.ID] = struct{ Value, Confidence float64 }{m.Value, m.Confidence}
	}
	return s
}

// --- Solicitudes de métricas ---

func (k *PostgresKnowledge) CreateMetricRequest(ctx context.Context, m domain.MetricRequest) (domain.MetricRequest, error) {
	if err := k.pool.QueryRow(ctx, `
INSERT INTO metric_requests (author_id, stroke, name, description) VALUES ($1::uuid, $2, $3, $4)
RETURNING id::text, status, created_at, `+named("$1::uuid"), m.AuthorID, m.Stroke, m.Name, m.Description).
		Scan(&m.ID, &m.Status, &m.CreatedAt, &m.AuthorName); err != nil {
		return domain.MetricRequest{}, fmt.Errorf("crear solicitud de métrica: %w", err)
	}
	return m, nil
}

func (k *PostgresKnowledge) MetricRequests(ctx context.Context, limit int) ([]domain.MetricRequest, error) {
	rows, err := k.pool.Query(ctx, `
SELECT m.id::text, m.author_id::text, `+named("m.author_id")+`, m.stroke, m.name, m.description, m.status, m.created_at
FROM metric_requests m ORDER BY m.created_at DESC LIMIT $1`, limit)
	if err != nil {
		return nil, fmt.Errorf("listar solicitudes de métricas: %w", err)
	}
	items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.MetricRequest, error) {
		var m domain.MetricRequest
		return m, row.Scan(&m.ID, &m.AuthorID, &m.AuthorName, &m.Stroke, &m.Name, &m.Description, &m.Status, &m.CreatedAt)
	})
	if err != nil {
		return nil, fmt.Errorf("leer solicitudes de métricas: %w", err)
	}
	return items, nil
}

func (k *PostgresKnowledge) Record(ctx context.Context, actorID *string, action, target string, details any) error {
	return audit(ctx, k.pool, actorID, action, target, details)
}

func statusStrings(in []domain.ProposalStatus) []string {
	out := make([]string, len(in))
	for i, s := range in {
		out[i] = string(s)
	}
	return out
}
