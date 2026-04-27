# Multi-tag categories migration — 2026-04-27

Runbook for the schema move from `knowledge_items.category TEXT` to
`TEXT[]` plus the v4-trimmed 56-category vocabulary. The Claude Code
sandbox can't reach Neon (egress to `*.neon.tech:443` and `:5432` is
blocked) so this runs from a host with Neon access.

The full brief lives in `docs/schema-migration-multitag-brief.md`. The
classifier prompt that the ingester loads is `docs/classifier-system-prompt-v1.md`.

## Files

| Order | File                              | What                                                          |
| ----- | --------------------------------- | ------------------------------------------------------------- |
| 0     | `0007_multitag_categories.sql`    | `category TEXT -> TEXT[]` + GIN index. Wraps existing values. |
| 0v    | `0007_verify.sql`                 | data_type=ARRAY, row counts unchanged, sample, GIN present.   |
| 1     | `0008_tag_ali_certs.sql`          | Bulk-tags every untagged ALI cert with `{specifications, industry-reference}`. Idempotent. |
| 2     | (Python) `scripts/retag-existing-documents.py` | Re-classifies the 272 ingested_pdf rows under v4-trimmed via Claude. |
| 3     | `0010_category_not_empty.sql`     | Adds `cardinality(category) >= 1` constraint after Steps 1-2 verify all rows are non-empty. |

There is no `0009`; the slot is reserved for the Python retag pass which
is a script, not a SQL file. Numbering left explicit to make the order
obvious.

## Run order

```bash
export DATABASE_URL='postgresql://…neon.tech/neondb?sslmode=require&channel_binding=require'
export ANTHROPIC_API_KEY='sk-ant-…'

cd scripts/migrations/2026-04-27-multitag-categories

# Phase 1 — schema
psql "$DATABASE_URL" -f 0007_multitag_categories.sql
psql "$DATABASE_URL" -f 0007_verify.sql      # eyeball: data_type=ARRAY, sample is {…}

# Phase 2 — bulk-tag ALI certs (no AI, fast)
psql "$DATABASE_URL" -f 0008_tag_ali_certs.sql

# Phase 3 — re-classify 272 ingested_pdf rows via Claude
cd ../../..   # back to repo root for bidiq imports
python scripts/retag-existing-documents.py --dry-run --limit 5   # spot-check first
python scripts/retag-existing-documents.py                        # full run

# Phase 4 — lock the invariant
cd scripts/migrations/2026-04-27-multitag-categories
psql "$DATABASE_URL" -f 0010_category_not_empty.sql
```

## Stop conditions

- `0007_verify` `data_type` is not `ARRAY` → migration didn't take. Abort.
- `0007_verify` row counts diverge from pre-migration → an UPDATE in 1b touched the wrong rows. Abort.
- `0008` exits with errors (CHECK violation, etc.) → likely the schema
  migration didn't run. Don't proceed.
- The Python retag prints a non-trivial number of `FAIL` lines → check
  network/auth/rate-limit and re-run; the script is idempotent (it
  rewrites whatever's currently there).
- `0010` raises `Refusing to add NOT EMPTY constraint: N rows have an empty category` →
  some rows are still untagged. Find them and tag them before re-running.

## Cost / time

- `0007`: seconds.
- `0008`: seconds (bulk UPDATE).
- `retag-existing-documents.py`: ~272 docs × ~$0.04 each on Sonnet (input is
  doc head + tail truncated to 50 k chars, output is small JSON) ≈ **\$10–\$15** total. ~30 min runtime.
- `0010`: seconds.

## Rollback

```sql
BEGIN;
ALTER TABLE knowledge_items DROP CONSTRAINT IF EXISTS knowledge_items_category_not_empty;
ALTER TABLE knowledge_items ADD COLUMN category_old TEXT;
UPDATE knowledge_items SET category_old = category[1];
ALTER TABLE knowledge_items DROP COLUMN category;
ALTER TABLE knowledge_items RENAME COLUMN category_old TO category;
DROP INDEX IF EXISTS idx_ki_category_gin;
COMMIT;
```

This loses multi-tag information. Combine with `git revert` of the PR to
roll the code back too.
