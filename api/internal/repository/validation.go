package repository

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"tennisvision/api/internal/domain"
	"tennisvision/api/internal/service"
)

// PostgresValidation persiste el corpus, las revisiones, las campañas Delphi y el ground truth.
type PostgresValidation struct {
	pool *pgxpool.Pool
}

func NewPostgresValidation(pool *pgxpool.Pool) *PostgresValidation {
	return &PostgresValidation{pool: pool}
}

// Un caso solo se usa mientras su dueño siga permitiendo la revisión experta.
const reviewable = `a.status = 'completed' AND a.expert_review_allowed AND a.report IS NOT NULL`

// --- Corpus ---

func (v *PostgresValidation) AddToCorpus(ctx context.Context, e domain.CorpusEntry) (domain.CorpusEntry, error) {
	err := v.pool.QueryRow(ctx, `
INSERT INTO corpus_cases (analysis_id, stroke, split, note, added_by) VALUES ($1::uuid, $2, $3, $4, $5::uuid)
RETURNING added_at`, e.AnalysisID, e.Stroke, e.Split, e.Note, e.AddedBy).Scan(&e.AddedAt)
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) && pgErr.Code == "23505" {
		return domain.CorpusEntry{}, fmt.Errorf("%w: el caso ya está en el corpus", domain.ErrConflict)
	}
	if err != nil {
		return domain.CorpusEntry{}, fmt.Errorf("agregar al corpus: %w", err)
	}
	return e, nil
}

func (v *PostgresValidation) RemoveFromCorpus(ctx context.Context, analysisID string) (bool, error) {
	tag, err := v.pool.Exec(ctx, `DELETE FROM corpus_cases WHERE analysis_id = $1::uuid`, analysisID)
	if err != nil {
		return false, fmt.Errorf("quitar del corpus: %w", err)
	}
	return tag.RowsAffected() == 1, nil
}

func (v *PostgresValidation) Corpus(ctx context.Context, stroke domain.StrokeType) ([]domain.CorpusEntry, error) {
	rows, err := v.pool.Query(ctx, `
SELECT c.analysis_id::text, c.stroke, c.split, c.note, c.added_by::text, c.added_at,
       (SELECT count(*) FROM case_ground_truth g WHERE g.analysis_id = c.analysis_id AND g.consensus)
FROM corpus_cases c JOIN analyses a ON a.id = c.analysis_id
WHERE ($1 = '' OR c.stroke = $1) AND `+reviewable+`
ORDER BY c.added_at DESC`, string(stroke))
	if err != nil {
		return nil, fmt.Errorf("listar corpus: %w", err)
	}
	items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.CorpusEntry, error) {
		var e domain.CorpusEntry
		return e, row.Scan(&e.AnalysisID, &e.Stroke, &e.Split, &e.Note, &e.AddedBy, &e.AddedAt, &e.GroundTruth)
	})
	if err != nil {
		return nil, fmt.Errorf("leer corpus: %w", err)
	}
	return items, nil
}

// --- Revisiones ---

// SaveReview reemplaza la revisión anterior del mismo experto, caso, campaña y ronda (y sus ítems).
func (v *PostgresValidation) SaveReview(ctx context.Context, r domain.CaseReview) (domain.CaseReview, error) {
	err := pgx.BeginFunc(ctx, v.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `
DELETE FROM case_reviews
WHERE analysis_id = $1::uuid AND reviewer_id = $2::uuid
  AND campaign_id IS NOT DISTINCT FROM $3::uuid AND round IS NOT DISTINCT FROM $4::int`,
			r.AnalysisID, r.ReviewerID, r.CampaignID, r.Round); err != nil {
			return fmt.Errorf("reemplazar revisión: %w", err)
		}
		if err := tx.QueryRow(ctx, `
INSERT INTO case_reviews (analysis_id, reviewer_id, campaign_id, round, player_level, notes, kb_version)
VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7) RETURNING id::text, submitted_at`,
			r.AnalysisID, r.ReviewerID, r.CampaignID, r.Round, r.PlayerLevel, r.Notes, r.KBVersion).
			Scan(&r.ID, &r.SubmittedAt); err != nil {
			return fmt.Errorf("guardar revisión: %w", err)
		}
		for _, it := range r.Items {
			if _, err := tx.Exec(ctx, `
INSERT INTO case_review_items (review_id, rule_id, rating, not_evaluable, verdict, system_fired, comment)
VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)`,
				r.ID, it.RuleID, it.Rating, it.NotEvaluable, it.Verdict, it.SystemFired, it.Comment); err != nil {
				return fmt.Errorf("guardar ítem de revisión: %w", err)
			}
		}
		return nil
	})
	return r, err
}

func (v *PostgresValidation) Reviews(ctx context.Context, f service.ReviewFilter) ([]domain.CaseReview, error) {
	rows, err := v.pool.Query(ctx, `
SELECT r.id::text, r.analysis_id::text, r.reviewer_id::text, r.campaign_id::text, r.round, r.player_level, r.notes,
       r.kb_version, r.submitted_at,
       COALESCE((SELECT jsonb_agg(jsonb_build_object('rule_id', i.rule_id, 'rating', i.rating,
                   'not_evaluable', i.not_evaluable, 'verdict', i.verdict, 'system_fired', i.system_fired,
                   'comment', i.comment) ORDER BY i.rule_id)
                 FROM case_review_items i WHERE i.review_id = r.id), '[]'::jsonb)
FROM case_reviews r JOIN analyses a ON a.id = r.analysis_id
WHERE ($1 = '' OR r.analysis_id = NULLIF($1, '')::uuid)
  AND ($2 = '' OR r.campaign_id = NULLIF($2, '')::uuid)
  AND ($3 = 0 OR r.round = $3)
  AND ($4 = '' OR r.reviewer_id = NULLIF($4, '')::uuid)
  AND (NOT $5 OR r.campaign_id IS NULL)
  AND ($6 = '' OR COALESCE(a.report->>'stroke_type', a.stroke_type) = $6)
ORDER BY r.submitted_at`,
		f.AnalysisID, f.CampaignID, f.Round, f.ReviewerID, f.FreeOnly, string(f.Stroke))
	if err != nil {
		return nil, fmt.Errorf("listar revisiones: %w", err)
	}
	items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.CaseReview, error) {
		var r domain.CaseReview
		var raw []byte
		if err := row.Scan(&r.ID, &r.AnalysisID, &r.ReviewerID, &r.CampaignID, &r.Round, &r.PlayerLevel, &r.Notes,
			&r.KBVersion, &r.SubmittedAt, &raw); err != nil {
			return r, err
		}
		return r, json.Unmarshal(raw, &r.Items)
	})
	if err != nil {
		return nil, fmt.Errorf("leer revisiones: %w", err)
	}
	return items, nil
}

// --- Campañas ---

const campaignColumns = `
SELECT c.id::text, c.stroke, c.title, c.rule_ids, c.status, c.current_round, c.max_rounds, c.consensus_pct::float8,
       c.min_raters, c.results, c.created_by::text, c.created_at, c.closed_at,
       COALESCE((SELECT array_agg(cc.analysis_id::text ORDER BY cc.analysis_id) FROM campaign_cases cc WHERE cc.campaign_id = c.id), '{}'),
       COALESCE((SELECT array_agg(cr.user_id::text ORDER BY cr.user_id) FROM campaign_raters cr WHERE cr.campaign_id = c.id), '{}')
FROM review_campaigns c`

func scanCampaign(row pgx.Row) (domain.Campaign, error) {
	var c domain.Campaign
	var results []byte
	if err := row.Scan(&c.ID, &c.Stroke, &c.Title, &c.RuleIDs, &c.Status, &c.CurrentRound, &c.MaxRounds, &c.ConsensusPct,
		&c.MinRaters, &results, &c.CreatedBy, &c.CreatedAt, &c.ClosedAt, &c.CaseIDs, &c.RaterIDs); err != nil {
		return domain.Campaign{}, err
	}
	if len(results) > 0 {
		c.Results = &domain.CampaignResults{}
		if err := json.Unmarshal(results, c.Results); err != nil {
			return domain.Campaign{}, fmt.Errorf("resultados ilegibles en %s: %w", c.ID, err)
		}
	}
	return c, nil
}

func (v *PostgresValidation) CreateCampaign(ctx context.Context, c domain.Campaign) (domain.Campaign, error) {
	var id string
	err := pgx.BeginFunc(ctx, v.pool, func(tx pgx.Tx) error {
		if err := tx.QueryRow(ctx, `
INSERT INTO review_campaigns (stroke, title, rule_ids, status, current_round, max_rounds, consensus_pct, min_raters, created_by)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::uuid) RETURNING id::text`,
			c.Stroke, c.Title, c.RuleIDs, c.Status, c.CurrentRound, c.MaxRounds, c.ConsensusPct, c.MinRaters, c.CreatedBy).
			Scan(&id); err != nil {
			return fmt.Errorf("crear campaña: %w", err)
		}
		if _, err := tx.Exec(ctx, `INSERT INTO campaign_cases (campaign_id, analysis_id) SELECT $1::uuid, unnest($2::uuid[])`,
			id, c.CaseIDs); err != nil {
			return fmt.Errorf("casos de la campaña: %w", err)
		}
		if _, err := tx.Exec(ctx, `INSERT INTO campaign_raters (campaign_id, user_id) SELECT $1::uuid, unnest($2::uuid[])`,
			id, c.RaterIDs); err != nil {
			return fmt.Errorf("evaluadores de la campaña: %w", err)
		}
		return nil
	})
	if err != nil {
		return domain.Campaign{}, err
	}
	return v.Campaign(ctx, id)
}

func (v *PostgresValidation) Campaign(ctx context.Context, id string) (domain.Campaign, error) {
	c, err := scanCampaign(v.pool.QueryRow(ctx, campaignColumns+` WHERE c.id = $1::uuid`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.Campaign{}, domain.ErrResourceNotFound
	}
	if err != nil {
		return domain.Campaign{}, fmt.Errorf("consultar campaña: %w", err)
	}
	return c, nil
}

func (v *PostgresValidation) Campaigns(ctx context.Context, stroke domain.StrokeType, limit int) ([]domain.Campaign, error) {
	rows, err := v.pool.Query(ctx, campaignColumns+` WHERE $1 = '' OR c.stroke = $1 ORDER BY c.created_at DESC LIMIT $2`,
		string(stroke), limit)
	if err != nil {
		return nil, fmt.Errorf("listar campañas: %w", err)
	}
	items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.Campaign, error) { return scanCampaign(row) })
	if err != nil {
		return nil, fmt.Errorf("leer campañas: %w", err)
	}
	return items, nil
}

func (v *PostgresValidation) AdvanceRound(ctx context.Context, id string, from int, actorID string) (bool, error) {
	ok := false
	err := pgx.BeginFunc(ctx, v.pool, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `
UPDATE review_campaigns SET current_round = current_round + 1
WHERE id = $1::uuid AND status = 'open' AND current_round = $2 AND current_round < max_rounds`, id, from)
		if err != nil {
			return fmt.Errorf("avanzar ronda: %w", err)
		}
		if ok = tag.RowsAffected() == 1; !ok {
			return nil
		}
		return audit(ctx, tx, &actorID, "campaign.advanced", "campaign:"+id, map[string]any{"round": from + 1})
	})
	return ok, err
}

func (v *PostgresValidation) CloseCampaign(ctx context.Context, id string, round int, results domain.CampaignResults,
	gt []domain.GroundTruth, actorID string) (bool, error) {
	payload, err := json.Marshal(results)
	if err != nil {
		return false, fmt.Errorf("serializar resultados: %w", err)
	}
	ok := false
	err = pgx.BeginFunc(ctx, v.pool, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `
UPDATE review_campaigns SET status = 'closed', closed_at = now(), results = $3
WHERE id = $1::uuid AND status = 'open' AND current_round = $2`, id, round, payload)
		if err != nil {
			return fmt.Errorf("cerrar campaña: %w", err)
		}
		if ok = tag.RowsAffected() == 1; !ok {
			return nil
		}
		// La última campaña que cubre un caso y una regla fija su ground truth vigente.
		for _, g := range gt {
			if _, err := tx.Exec(ctx, `
INSERT INTO case_ground_truth (analysis_id, rule_id, campaign_id, median, label, consensus, raters)
VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6, $7)
ON CONFLICT (analysis_id, rule_id) DO UPDATE SET campaign_id = EXCLUDED.campaign_id, median = EXCLUDED.median,
    label = EXCLUDED.label, consensus = EXCLUDED.consensus, raters = EXCLUDED.raters, decided_at = now()`,
				g.AnalysisID, g.RuleID, g.CampaignID, g.Median, g.Label, g.Consensus, g.Raters); err != nil {
				return fmt.Errorf("guardar ground truth: %w", err)
			}
		}
		return audit(ctx, tx, &actorID, "campaign.closed", "campaign:"+id,
			map[string]any{"round": round, "ground_truth_items": len(gt)})
	})
	return ok, err
}

// --- Ground truth y profesionales ---

func (v *PostgresValidation) LabeledSamples(ctx context.Context, stroke domain.StrokeType, split string) ([]domain.LabeledSample, error) {
	rows, err := v.pool.Query(ctx, `
SELECT a.id::text, a.camera_view, a.report->'metrics', g.rule_id, g.label = 'present'
FROM case_ground_truth g
JOIN corpus_cases c ON c.analysis_id = g.analysis_id
JOIN analyses a ON a.id = g.analysis_id
WHERE c.stroke = $1 AND ($2 = '' OR c.split = $2) AND g.consensus AND g.label <> 'indeterminate' AND `+reviewable,
		string(stroke), split)
	if err != nil {
		return nil, fmt.Errorf("leer ground truth: %w", err)
	}
	items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.LabeledSample, error) {
		var ls domain.LabeledSample
		var raw []byte
		if err := row.Scan(&ls.ID, &ls.View, &raw, &ls.RuleID, &ls.Present); err != nil {
			return ls, err
		}
		ls.Metrics = parseMetrics(raw)
		return ls, nil
	})
	if err != nil {
		return nil, fmt.Errorf("leer ground truth: %w", err)
	}
	return items, nil
}

func (v *PostgresValidation) Professionals(ctx context.Context) ([]service.Professional, error) {
	rows, err := v.pool.Query(ctx, `
SELECT u.id::text, `+named("u.id")+`, array_agg(r.role ORDER BY r.role)
FROM users u JOIN user_roles r ON r.user_id = u.id
WHERE u.status = 'active' AND r.role IN ('expert', 'validator')
GROUP BY u.id ORDER BY 2`)
	if err != nil {
		return nil, fmt.Errorf("listar profesionales: %w", err)
	}
	items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (service.Professional, error) {
		var p service.Professional
		var roles []string
		if err := row.Scan(&p.ID, &p.Name, &roles); err != nil {
			return p, err
		}
		for _, r := range roles {
			p.Roles = append(p.Roles, domain.Role(r))
		}
		return p, nil
	})
	if err != nil {
		return nil, fmt.Errorf("leer profesionales: %w", err)
	}
	return items, nil
}

func (v *PostgresValidation) Record(ctx context.Context, actorID *string, action, target string, details any) error {
	return audit(ctx, v.pool, actorID, action, target, details)
}
