-- Base de conocimiento gobernada: el experto propone, el validador aprueba y la aprobación publica
-- una versión inmutable (kb_releases) que el worker carga sin reiniciar.

-- Catálogo de métricas que el worker sabe medir (lo escribe el worker al arrancar).
CREATE TABLE IF NOT EXISTS metric_catalog (
    stroke     TEXT NOT NULL CHECK (stroke IN ('forehand', 'backhand', 'serve', 'volley')),
    metric_id  TEXT NOT NULL,
    label      TEXT NOT NULL,
    unit       TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (stroke, metric_id)
);

CREATE TABLE IF NOT EXISTS kb_proposals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    author_id       UUID NOT NULL REFERENCES users (id),
    stroke          TEXT NOT NULL CHECK (stroke IN ('forehand', 'backhand', 'serve', 'volley')),
    title           TEXT NOT NULL,
    rationale       TEXT NOT NULL,
    evidence        TEXT NOT NULL DEFAULT '',
    changes         JSONB NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('draft', 'submitted', 'changes_requested', 'approved', 'rejected', 'withdrawn')),
    base_release_id UUID NULL,
    decided_by      UUID NULL REFERENCES users (id),
    decided_at      TIMESTAMPTZ NULL,
    decision_note   TEXT NOT NULL DEFAULT '',
    release_id      UUID NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Nadie valida lo suyo: también lo garantiza la base de datos.
    CONSTRAINT kb_proposals_four_eyes CHECK (decided_by IS NULL OR decided_by <> author_id)
);

CREATE INDEX IF NOT EXISTS kb_proposals_status_idx ON kb_proposals (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS kb_proposals_author_idx ON kb_proposals (author_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS kb_proposal_comments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id UUID NOT NULL REFERENCES kb_proposals (id) ON DELETE CASCADE,
    author_id   UUID NOT NULL REFERENCES users (id),
    body        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS kb_proposal_comments_idx ON kb_proposal_comments (proposal_id, created_at);

-- Versiones publicadas: inmutables salvo `active` (rollback) y `status` (el worker marca 'invalid').
CREATE TABLE IF NOT EXISTS kb_releases (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stroke         TEXT NOT NULL CHECK (stroke IN ('forehand', 'backhand', 'serve', 'volley')),
    version        TEXT NOT NULL,
    snapshot       JSONB NOT NULL,
    changelog      TEXT NOT NULL DEFAULT '',
    proposal_id    UUID NULL REFERENCES kb_proposals (id),
    proposed_by    UUID NULL REFERENCES users (id),
    validated_by   UUID NULL REFERENCES users (id),
    active         BOOLEAN NOT NULL DEFAULT false,
    status         TEXT NOT NULL DEFAULT 'valid' CHECK (status IN ('valid', 'invalid')),
    invalid_reason TEXT NULL,
    activated_at   TIMESTAMPTZ NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (stroke, version)
);

-- Una sola versión activa por golpe.
CREATE UNIQUE INDEX IF NOT EXISTS kb_releases_one_active_idx ON kb_releases (stroke) WHERE active;

ALTER TABLE kb_proposals
    ADD CONSTRAINT kb_proposals_base_fk FOREIGN KEY (base_release_id) REFERENCES kb_releases (id),
    ADD CONSTRAINT kb_proposals_release_fk FOREIGN KEY (release_id) REFERENCES kb_releases (id);

-- Métricas nuevas que piden los expertos (las implementa ingeniería en el worker).
CREATE TABLE IF NOT EXISTS metric_requests (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    author_id   UUID NOT NULL REFERENCES users (id),
    stroke      TEXT NOT NULL CHECK (stroke IN ('forehand', 'backhand', 'serve', 'volley')),
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'planned', 'done', 'declined')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Previsualización de impacto: últimos análisis completados por golpe.
CREATE INDEX IF NOT EXISTS analyses_completed_stroke_idx ON analyses (stroke_type, created_at DESC)
    WHERE status = 'completed';
