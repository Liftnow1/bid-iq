-- 0007_multitag_categories.sql
-- Migration: knowledge_items.category TEXT -> TEXT[]
-- Date: 2026-04-27
--
-- Replaces the single-value category column with an array column so a
-- document can carry multiple tags from the v4-trimmed 56-category
-- vocabulary (see docs/classifier-system-prompt-v1.md).
--
-- Existing values are wrapped: 'installation-guides' -> {installation-guides}.
-- The not-empty constraint is added later in 0010 after re-tagging
-- (Steps 5-6) confirms every row has at least one valid tag.

BEGIN;

-- 1a: new array column.
ALTER TABLE knowledge_items
  ADD COLUMN category_new TEXT[];

-- 1b: wrap existing single value into a one-element array.
UPDATE knowledge_items
   SET category_new = ARRAY[category]
 WHERE category IS NOT NULL AND category <> '';

UPDATE knowledge_items
   SET category_new = ARRAY[]::TEXT[]
 WHERE category IS NULL OR category = '';

-- 1c: swap the columns.
ALTER TABLE knowledge_items DROP COLUMN category;
ALTER TABLE knowledge_items RENAME COLUMN category_new TO category;

-- 1d: GIN index for array containment / overlap queries.
CREATE INDEX IF NOT EXISTS idx_ki_category_gin
  ON knowledge_items USING GIN (category);

COMMIT;
