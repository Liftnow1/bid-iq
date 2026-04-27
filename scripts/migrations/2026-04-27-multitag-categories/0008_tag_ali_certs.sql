-- 0008_tag_ali_certs.sql
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
