-- Usuarios identificados por su proveedor OIDC. `sub` solo es único dentro de su emisor,
-- así que la identidad estable es (issuer, subject). El id interno desacopla el esquema del IdP.
CREATE TABLE IF NOT EXISTS users (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issuer     TEXT NOT NULL,
    subject    TEXT NOT NULL,
    email      TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (issuer, subject)
);

-- Los análisis anteriores a la autenticación quedan con user_id NULL (no los ve nadie).
ALTER TABLE analyses
    ADD CONSTRAINT analyses_user_fk FOREIGN KEY (user_id) REFERENCES users (id);

-- Sirve al listado paginado "mis análisis" (WHERE user_id ORDER BY created_at DESC, id DESC).
DROP INDEX IF EXISTS analyses_user_idx;
CREATE INDEX IF NOT EXISTS analyses_user_created_idx ON analyses (user_id, created_at DESC, id DESC);
