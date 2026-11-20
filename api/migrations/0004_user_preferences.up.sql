-- Preferencias del usuario que los clientes leen al iniciar sesión (web y móvil comparten el valor).
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS handedness TEXT NOT NULL DEFAULT 'right'
        CONSTRAINT users_handedness_check CHECK (handedness IN ('right', 'left'));
