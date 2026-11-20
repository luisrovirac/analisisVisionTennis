DROP INDEX IF EXISTS analyses_completed_stroke_idx;
DROP TABLE IF EXISTS metric_requests;
ALTER TABLE kb_proposals DROP CONSTRAINT IF EXISTS kb_proposals_base_fk, DROP CONSTRAINT IF EXISTS kb_proposals_release_fk;
DROP TABLE IF EXISTS kb_releases;
DROP TABLE IF EXISTS kb_proposal_comments;
DROP TABLE IF EXISTS kb_proposals;
DROP TABLE IF EXISTS metric_catalog;
