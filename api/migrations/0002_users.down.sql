DROP INDEX IF EXISTS analyses_user_created_idx;
CREATE INDEX IF NOT EXISTS analyses_user_idx ON analyses (user_id) WHERE user_id IS NOT NULL;
ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_user_fk;
DROP TABLE IF EXISTS users;
