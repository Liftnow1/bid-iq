-- Rebuild search_text from full raw_content for all knowledge_items rows.
--
-- Why: search_text was previously built as
--      title + summary + tags + raw_content[:5000]
-- which capped the indexed body at 5,000 characters. Long install
-- manuals (50+ pages) ended up with only ~6% of their body indexed in
-- Postgres FTS, so queries about specific install specs (anchor
-- pattern, concrete depth, electrical requirements, bolt patterns)
-- couldn't find the right document. The Python ingester
-- (bidiq/ingest.py) and the Next.js ingest route
-- (app/api/knowledge-base/ingest/route.ts) have both been updated to
-- index the full raw_content. This script backfills rows already in the
-- table.
--
-- Safety: idempotent. Running it twice produces the same result.
-- Tier-1 rows have raw_content NULL — the COALESCE ensures their
-- search_text remains title + summary + tags only, unchanged.
--
-- Run via Neon SQL Editor or `psql $DATABASE_URL -f <this-file>`.

UPDATE knowledge_items
   SET search_text = COALESCE(title, '') || ' ' ||
                     COALESCE(summary, '') || ' ' ||
                     COALESCE(array_to_string(tags, ' '), '') || ' ' ||
                     COALESCE(raw_content, '');

-- The GIN index name from lib/db.ts / scripts/setup-db.mjs is
-- idx_ki_search. REINDEX is optional — the GIN index keeps itself in
-- sync via the UPDATE above — but rebuilding compacts dead tuples and
-- is cheap on the size of this table.
REINDEX INDEX IF EXISTS idx_ki_search;

-- Verification queries (run by hand, not part of the migration):
--   SELECT count(*) FROM knowledge_items;
--   SELECT id, length(search_text) AS st_len, length(raw_content) AS rc_len
--     FROM knowledge_items WHERE id IN (3324, 3325, 3326, 3336)
--    ORDER BY id;
--   -- For row 3336 (75-page Challenger 4018 manual) expect:
--   --   rc_len ~84,026, st_len ~84,000+, search_text ILIKE '%anchor%' = true
