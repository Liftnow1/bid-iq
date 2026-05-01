-- 0009_retag_ali_certs_v2.sql
-- Re-tag every ali_certification row under the v2 3-tier vocabulary.
--
-- Background: 0008 originally tagged these rows {specifications,
-- industry-reference} from the v4-trimmed 56-category set. Both of
-- those values are invalid under the v2 access-model vocabulary in
-- docs/classifier-system-prompt-v2.md. ALI cert entries are public-
-- safe industry reference data, so they all belong in tier-1-public.
--
-- Idempotent: re-runs are safe (always sets {tier-1-public}). Targets
-- every ali_certification row whether 0008 ran first or not.
--
-- Run after 0007_multitag_categories.sql. Supersedes 0008.

BEGIN;

UPDATE knowledge_items
   SET category = ARRAY['tier-1-public']
 WHERE source_type = 'ali_certification';

COMMIT;

-- Verification (run after the COMMIT):
--   SELECT category, count(*)::int AS n
--     FROM knowledge_items
--    WHERE source_type = 'ali_certification'
--    GROUP BY category;
--   -- expect a single row: {tier-1-public}, count ~3058
