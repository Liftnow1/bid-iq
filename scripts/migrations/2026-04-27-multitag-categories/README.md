# Multi-tag categories migration — 2026-04-27

Runbook for the schema move from `knowledge_items.category TEXT` to
`TEXT[]`. This folder spans two vocabulary eras:

- 0007 + 0010 are vocabulary-agnostic (schema swap + not-empty check).
- 0008 used the original v4-trimmed 56-category vocabulary and is now
  **superseded by 0009**, which uses the v2 3-tier access model. Run
  0009; skip 0008.

The Claude Code sandbox can't reach Neon (egress to `*.neon.tech:443`
and `:5432` is blocked) so this runs from a host with Neon access.

The original brief lives at `docs/schema-migration-multitag-brief.md`.
The active classifier prompt is `docs/classifier-system-prompt-v2.md`
(loaded by `bidiq/ingest.py`); the v1 prompt has been moved to
`docs/classifier-system-prompt-v1-DEPRECATED.md`.

## Files

| Order | File                              | What                                                          |
| ----- | --------------------------------- | ------------------------------------------------------------- |
| 0     | `0007_multitag_categories.sql`    | `category TEXT -> TEXT[]` + GIN index. Wraps existing values. |
| 0v    | `0007_verify.sql`                 | data_type=ARRAY, row counts unchanged, sample, GIN present.   |
| —     | ~~`0008_tag_ali_certs.sql`~~      | **Deprecated.** Tagged ALI rows under the retired v4-trimmed vocabulary. Do not run. |
| 1     | `0009_retag_ali_certs_v2.sql`     | Sets every `ali_certification` row to `{tier-1-public}` under the v2 vocabulary. Idempotent; supersedes 0008. |
| 2     | (Python) `scripts/retag-existing-documents.py` | Re-classifies ingested_pdf rows under the active classifier prompt via Claude. **Currently deferred** — the v2 swap PR explicitly does not run this. |
| 3     | `0010_category_not_empty.sql`     | Adds `cardinality(category) >= 1` constraint after Steps 1-2 verify all rows are non-empty. |

## Run order

```bash
export DATABASE_URL='postgresql://…neon.tech/neondb?sslmode=require&channel_binding=require'
export ANTHROPIC_API_KEY='sk-ant-…'

cd scripts/migrations/2026-04-27-multitag-categories

# Phase 1 — schema
psql "$DATABASE_URL" -f 0007_multitag_categories.sql
psql "$DATABASE_URL" -f 0007_verify.sql      # eyeball: data_type=ARRAY, sample is {…}

# Phase 2 — re-tag ALI certs to tier-1-public (skip 0008; run 0009)
psql "$DATABASE_URL" -f 0009_retag_ali_certs_v2.sql

# Phase 3 — re-classify ingested_pdf rows via Claude
# DEFERRED in the v2 swap PR. Will be run in waves keyed to specific
# corpus-ingest needs. When you do run it:
#   cd ../../..
#   python scripts/retag-existing-documents.py --dry-run --limit 5
#   python scripts/retag-existing-documents.py
#   cd scripts/migrations/2026-04-27-multitag-categories

# Phase 4 — lock the invariant (run after Phase 2 succeeds; the
# pre-flight DO block in 0010 will refuse to add the constraint if any
# row is still empty, including untagged ingested_pdf rows from Phase 3
# being deferred — so for now expect 0010 to abort cleanly until the
# Python retag runs).
psql "$DATABASE_URL" -f 0010_category_not_empty.sql
```

## Stop conditions

- `0007_verify` `data_type` is not `ARRAY` → migration didn't take. Abort.
- `0007_verify` row counts diverge from pre-migration → an UPDATE in 1b touched the wrong rows. Abort.
- `0009` exits with errors (table missing, schema mismatch, etc.) → likely
  the schema migration didn't run. Don't proceed.
- The Python retag (when you run it later) prints a non-trivial number of
  `FAIL` lines → check network/auth/rate-limit and re-run; the script is
  idempotent (it rewrites whatever's currently there).
- `0010` raises `Refusing to add NOT EMPTY constraint: N rows have an empty category` →
  some rows are still untagged. Expected today since the Python retag is
  deferred. Tag them and re-run.

## Cost / time

- `0007`: seconds.
- `0009`: seconds (bulk UPDATE on ~3,058 rows).
- `retag-existing-documents.py`: under v2 the prompt is much smaller
  (~8 k chars vs v1's ~37 k), so per-doc cost drops correspondingly —
  rough order-of-magnitude $2–4 across 272 docs, ~15–25 min runtime.
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
