-- Roles y gobierno de usuarios. Todo usuario autenticado es "usuario"; los roles adicionales se
-- acumulan (una persona puede ser experto y validador a la vez).
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'
        CONSTRAINT users_status_check CHECK (status IN ('active', 'suspended'));

CREATE INDEX IF NOT EXISTS users_email_lower_idx ON users (lower(email));

CREATE TABLE IF NOT EXISTS user_roles (
    user_id    UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('admin', 'expert', 'validator')),
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role)
);

-- Perfil profesional (expertos y validadores). Solo se ve dentro del portal.
CREATE TABLE IF NOT EXISTS professional_profiles (
    user_id        UUID PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    display_name   TEXT NOT NULL,
    specialty      TEXT NOT NULL CHECK (specialty IN ('tennis_coach', 'biomechanics', 'physio', 'fitness')),
    certifications TEXT NOT NULL DEFAULT '',
    bio            TEXT NOT NULL DEFAULT '',
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Una invitación reserva roles para un email; se acepta al iniciar sesión con ese email verificado.
CREATE TABLE IF NOT EXISTS invitations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT NOT NULL,
    roles       TEXT[] NOT NULL CHECK (cardinality(roles) > 0 AND roles <@ ARRAY['admin', 'expert', 'validator']),
    invited_by  UUID NULL REFERENCES users (id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ NULL,
    accepted_by UUID NULL REFERENCES users (id) ON DELETE SET NULL,
    revoked_at  TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS invitations_pending_idx ON invitations (lower(email))
    WHERE accepted_at IS NULL AND revoked_at IS NULL;

-- Quién hizo qué: cambios de rol, invitaciones, decisiones de validación, publicaciones, rollbacks.
CREATE TABLE IF NOT EXISTS audit_log (
    id         BIGSERIAL PRIMARY KEY,
    actor_id   UUID NULL REFERENCES users (id) ON DELETE SET NULL,
    action     TEXT NOT NULL,
    target     TEXT NOT NULL DEFAULT '',
    details    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_log_created_idx ON audit_log (created_at DESC);

-- Checkbox "Permitir que expertos vean este video", marcado por defecto.
ALTER TABLE analyses
    ADD COLUMN IF NOT EXISTS expert_review_allowed BOOLEAN NOT NULL DEFAULT true;
