-- 0008_tag_ali_certs.sql
--
-- DEPRECATED 2026-04-27 — superseded by 0009_retag_ali_certs_v2.sql.
-- Do NOT run this file. Both 'specifications' and 'industry-reference'
-- are invalid under the v2 access-model vocabulary
-- (docs/classifier-system-prompt-v2.md). 0009 re-tags every
-- ali_certification row to {tier-1-public}. Retained as a historical
-- record of what the v4-trimmed migration intended to do.
--
-- Original purpose:
-- Tags every source_type='ali_certification' row that's missing a
-- category with the dual-tag {specifications, industry-reference}.
-- ALI cert entries are both technical specs (compliance with ALI ALCTV
-- standards) and trade-association reference material.
--
-- Idempotent: rows that already have any category are left alone.
-- Run after 0007_multitag_categories.sql.

BEGIN;

UPDATE knowledge_items
   SET category = ARRAY['specifications', 'industry-reference']
 WHERE source_type = 'ali_certification'
   AND (category IS NULL OR cardinality(category) = 0);

COMMIT;

-- Verification (run after the COMMIT):
--   SELECT count(*)::int AS untagged_ali_certs
--     FROM knowledge_items
--    WHERE source_type = 'ali_certification'
--      AND (category IS NULL OR cardinality(category) = 0);
--   -- expect 0
