-- 0010_category_not_empty.sql
-- Lock the not-empty invariant on knowledge_items.category.
-- Run AFTER both retag passes (0008 for ALI rows + the Python retag
-- script for the 272 ingested_pdf rows) succeed.
--
-- Pre-flight: the SELECT below must return 0 before the ALTER is run.
-- If it doesn't, abort and chase the offending rows.

BEGIN;

-- Pre-flight check — abort with a noisy error if any row is still empty.
DO $$
DECLARE
  empties INT;
BEGIN
  SELECT count(*) INTO empties
    FROM knowledge_items
   WHERE category IS NULL OR cardinality(category) = 0;
  IF empties > 0 THEN
    RAISE EXCEPTION 'Refusing to add NOT EMPTY constraint: % rows have an empty category', empties;
  END IF;
END$$;

ALTER TABLE knowledge_items
  ADD CONSTRAINT knowledge_items_category_not_empty
  CHECK (cardinality(category) >= 1);

COMMIT;
