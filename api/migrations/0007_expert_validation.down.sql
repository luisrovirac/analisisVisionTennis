ALTER TABLE kb_releases DROP COLUMN IF EXISTS bump, DROP COLUMN IF EXISTS changelog_entries;
DROP TABLE IF EXISTS case_ground_truth;
DROP TABLE IF EXISTS case_review_items;
DROP TABLE IF EXISTS case_reviews;
DROP TABLE IF EXISTS campaign_raters;
DROP TABLE IF EXISTS campaign_cases;
DROP TABLE IF EXISTS review_campaigns;
DROP TABLE IF EXISTS corpus_cases;
