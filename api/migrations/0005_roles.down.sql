ALTER TABLE analyses DROP COLUMN IF EXISTS expert_review_allowed;
DROP TABLE IF EXISTS audit_log;
DROP TABLE IF EXISTS invitations;
DROP TABLE IF EXISTS professional_profiles;
DROP TABLE IF EXISTS user_roles;
DROP INDEX IF EXISTS users_email_lower_idx;
ALTER TABLE users DROP COLUMN IF EXISTS status, DROP COLUMN IF EXISTS email_verified;
