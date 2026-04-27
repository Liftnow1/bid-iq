-- Verification for 0007. Each query has its expected output noted.

-- (a) data_type should be 'ARRAY'.
SELECT data_type
  FROM information_schema.columns
 WHERE table_name = 'knowledge_items' AND column_name = 'category';

-- (b) row counts by source_type unchanged from pre-migration.
SELECT source_type, count(*)::int AS n
  FROM knowledge_items
 GROUP BY source_type
 ORDER BY source_type;

-- (c) sample ingested_pdf rows: category is now an array (e.g. {installation-guides}).
SELECT id, category, source_type
  FROM knowledge_items
 WHERE source_type = 'ingested_pdf'
 LIMIT 5;

-- (d) GIN index exists.
SELECT indexname
  FROM pg_indexes
 WHERE tablename = 'knowledge_items' AND indexname = 'idx_ki_category_gin';
