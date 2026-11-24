-- Validación formal del conocimiento experto: corpus de referencia, revisiones por regla (libres y en
-- campañas Delphi ciegas), ground truth de consenso y changelog estructurado de cada versión.
-- Las revisiones son control de calidad de las reglas: nunca cambian el análisis del usuario.

-- Corpus de validación: análisis elegidos para calibrar (calibration) o medir (test) las reglas.
CREATE TABLE IF NOT EXISTS corpus_cases (
    analysis_id UUID PRIMARY KEY REFERENCES analyses (id) ON DELETE CASCADE,
    stroke      TEXT NOT NULL CHECK (stroke IN ('forehand', 'backhand', 'serve', 'volley')),
    split       TEXT NOT NULL CHECK (split IN ('calibration', 'test')),
    note        TEXT NOT NULL DEFAULT '',
    added_by    UUID NOT NULL REFERENCES users (id),
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS corpus_cases_stroke_idx ON corpus_cases (stroke, split);

-- Campañas de calificación ciega con rondas Delphi. El consenso se define antes de empezar.
CREATE TABLE IF NOT EXISTS review_campaigns (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stroke        TEXT NOT NULL CHECK (stroke IN ('forehand', 'backhand', 'serve', 'volley')),
    title         TEXT NOT NULL,
    rule_ids      TEXT[] NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    current_round INT NOT NULL DEFAULT 1 CHECK (current_round >= 1),
    max_rounds    INT NOT NULL DEFAULT 3 CHECK (max_rounds BETWEEN 1 AND 5),
    consensus_pct NUMERIC(4, 3) NOT NULL DEFAULT 0.700 CHECK (consensus_pct > 0 AND consensus_pct <= 1),
    min_raters    INT NOT NULL DEFAULT 3 CHECK (min_raters >= 2),
    results       JSONB NULL, -- α y consenso por regla al cerrar
    created_by    UUID NOT NULL REFERENCES users (id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at     TIMESTAMPTZ NULL,
    CHECK (current_round <= max_rounds)
);

CREATE TABLE IF NOT EXISTS campaign_cases (
    campaign_id UUID NOT NULL REFERENCES review_campaigns (id) ON DELETE CASCADE,
    analysis_id UUID NOT NULL REFERENCES analyses (id) ON DELETE CASCADE,
    PRIMARY KEY (campaign_id, analysis_id)
);

CREATE TABLE IF NOT EXISTS campaign_raters (
    campaign_id UUID NOT NULL REFERENCES review_campaigns (id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users (id),
    PRIMARY KEY (campaign_id, user_id)
);

-- Una revisión = un experto sobre un caso. campaign_id NULL = revisión libre (editable, una por caso).
CREATE TABLE IF NOT EXISTS case_reviews (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_id  UUID NOT NULL REFERENCES analyses (id) ON DELETE CASCADE,
    reviewer_id  UUID NOT NULL REFERENCES users (id),
    campaign_id  UUID NULL REFERENCES review_campaigns (id) ON DELETE CASCADE,
    round        INT NULL,
    player_level TEXT NULL CHECK (player_level IN ('beginner', 'intermediate', 'advanced', 'pro')),
    notes        TEXT NOT NULL DEFAULT '',
    kb_version   TEXT NULL,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((campaign_id IS NULL) = (round IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS case_reviews_free_idx ON case_reviews (analysis_id, reviewer_id)
    WHERE campaign_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS case_reviews_campaign_idx ON case_reviews (campaign_id, round, analysis_id, reviewer_id)
    WHERE campaign_id IS NOT NULL;

-- Calificación 1-7 del aspecto que evalúa la regla (o «no evaluable») y veredicto sobre el sistema.
CREATE TABLE IF NOT EXISTS case_review_items (
    review_id     UUID NOT NULL REFERENCES case_reviews (id) ON DELETE CASCADE,
    rule_id       TEXT NOT NULL,
    rating        SMALLINT NULL CHECK (rating BETWEEN 1 AND 7),
    not_evaluable BOOLEAN NOT NULL DEFAULT false,
    verdict       TEXT NULL CHECK (verdict IN ('correct', 'false_positive', 'missed', 'unsure')),
    system_fired  BOOLEAN NULL, -- lo que dijo el sistema al revisar (congelado)
    comment       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (review_id, rule_id),
    CHECK (NOT (not_evaluable AND rating IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS case_review_items_rule_idx ON case_review_items (rule_id);

-- Ground truth vigente por caso y regla: lo fija el cierre de la última campaña que los cubrió.
CREATE TABLE IF NOT EXISTS case_ground_truth (
    analysis_id UUID NOT NULL REFERENCES analyses (id) ON DELETE CASCADE,
    rule_id     TEXT NOT NULL,
    campaign_id UUID NOT NULL REFERENCES review_campaigns (id) ON DELETE CASCADE,
    median      NUMERIC(3, 1) NOT NULL,
    label       TEXT NOT NULL CHECK (label IN ('present', 'absent', 'indeterminate')),
    consensus   BOOLEAN NOT NULL,
    raters      INT NOT NULL,
    decided_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (analysis_id, rule_id)
);

-- Changelog estructurado y tipo de salto semver de cada versión publicada.
ALTER TABLE kb_releases
    ADD COLUMN IF NOT EXISTS changelog_entries JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS bump TEXT NULL CHECK (bump IN ('major', 'minor', 'patch'));
