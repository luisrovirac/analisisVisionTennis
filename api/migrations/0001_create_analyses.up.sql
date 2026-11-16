CREATE TABLE IF NOT EXISTS analyses (
    id            UUID PRIMARY KEY,
    user_id       UUID NULL,
    stroke_type   TEXT NOT NULL CHECK (stroke_type IN ('forehand', 'backhand', 'serve', 'volley')),
    handedness    TEXT NOT NULL CHECK (handedness IN ('right', 'left')),
    camera_view   TEXT NOT NULL CHECK (camera_view IN ('side', 'front', 'back')),
    status        TEXT NOT NULL CHECK (status IN ('awaiting_upload', 'queued', 'processing', 'completed', 'failed')),
    video_key     TEXT NOT NULL,
    attempts      INT  NOT NULL DEFAULT 0,
    error_code    TEXT NULL,
    error_message TEXT NULL,
    report        JSONB NULL,
    kb_version    TEXT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS analyses_status_idx ON analyses (status);
CREATE INDEX IF NOT EXISTS analyses_user_idx ON analyses (user_id) WHERE user_id IS NOT NULL;
