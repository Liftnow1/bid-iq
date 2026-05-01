# Claude Code Brief: Swap Classifier v1 → v2 (56 categories → 3 tiers)

**Audience:** Claude Code
**Repo:** github.com/Liftnow1/bid-iq
**Branch:** create new — `claude/classifier-v2-3tier`
**Estimated effort:** ~30-45 min, $0 (no API costs in this PR; re-tagging deferred)

---

## TL;DR

Replace the 56-category classifier vocabulary (v1) with a 3-tier access model (v2). The v1 prompt was over-engineered scope creep that doesn't align with the operating manual. The v2 model is what we actually need: tier-1-public for content engine, tier-2-internal for email agent, tier-3-paul-only for nothing.

This is a minimal swap:
- Replace classifier prompt content
- Update VALID_CATEGORIES set in ingester (4 values now: 3 tiers + uncategorized)
- Update lib/category-tiers.ts to reference new tier names
- Update retag script to load new prompt
- Update README/INGEST docs

**Do NOT bulk-retag the existing 272 ingested PDFs in this PR.** Re-tagging waits until we ingest the Phase 2 staged corpus in waves. This PR is just the swap.

---

## What changes

### File 1: `docs/classifier-system-prompt-v1.md` → `docs/classifier-system-prompt-v2.md`

The new file lives at `docs/classifier-system-prompt-v2.md`. It is a separate document I'm providing — paste its full contents in.

After committing the new file, mark the old v1 file as deprecated:
- Rename `docs/classifier-system-prompt-v1.md` → `docs/classifier-system-prompt-v1-DEPRECATED.md`
- Add a header banner at the top:
  ```
  > **DEPRECATED 2026-04-27** — superseded by `classifier-system-prompt-v2.md` (3-tier access model).
  > Retained for historical reference only. Do not use for new ingestion.
  ```

### File 2: `bidiq/ingest.py`

Update the classifier prompt loader:

```python
# OLD
CLASSIFIER_PROMPT_PATH = "docs/classifier-system-prompt-v1.md"

# NEW
CLASSIFIER_PROMPT_PATH = "docs/classifier-system-prompt-v2.md"
```

Update the VALID_CATEGORIES set. The old set had 57 values (56 categories + uncategorized). The new set has 4:

```python
VALID_CATEGORIES = {
    "tier-1-public",
    "tier-2-internal",
    "tier-3-paul-only",
    "uncategorized",
}
```

Update the variable name where it makes sense — `VALID_CATEGORIES` is fine, but if you prefer rename to `VALID_TIERS` for clarity, do so consistently. Either works.

The classify_document() function logic stays the same — it loads the prompt, sends doc text, parses JSON array response, validates against VALID_CATEGORIES, falls back to `["uncategorized"]` on errors. No structural changes.

### File 3: `lib/category-tiers.ts`

This file already exists from PR #15 with constants for `PAUL_ONLY`, `EMAIL_AGENT_EXCLUDE`, `CONTENT_ENGINE_ALLOW`. Update the values to match the 3-tier model:

```typescript
// Tier-3: Paul-only — no agent ever sees these
export const PAUL_ONLY: string[] = ["tier-3-paul-only"];

// Email agent (and future bid tracker) excludes only tier-3
export const EMAIL_AGENT_EXCLUDE: string[] = ["tier-3-paul-only"];

// Content engine sees tier-1 only (excludes tier-2 and tier-3)
export const CONTENT_ENGINE_ALLOW: string[] = ["tier-1-public"];

// Convenience: full tier list for filtering UI
export const ALL_TIERS: string[] = [
  "tier-1-public",
  "tier-2-internal",
  "tier-3-paul-only",
];
```

These constants are still not enforced at the API level today (per PR #15 notes) — they document intent for future agent-tier filtering. That stays the same.

### File 4: `scripts/retag-existing-documents.py`

The script imports `classify_document` from `bidiq.ingest`, so once the ingester is updated, the retag script automatically uses the new prompt. No code changes needed in this script itself.

**Important:** Do NOT run the retag script as part of this PR. It would re-classify all 272 existing ingested_pdf rows under the new 3-tier model, costing API tokens and running ~30 minutes. We're deferring re-tagging until we ingest the Phase 2 staged corpus in waves. This PR ships only the prompt + code change.

If anyone runs the retag script after this PR merges, it'll work correctly — it'll just produce 3-tier values for all 272 rows.

### File 5: `scripts/migrations/2026-04-27-multitag-categories/0008_tag_ali_certs.sql`

The 0008 migration tags ALI cert rows as `{specifications, industry-reference}`. Both of those values are now invalid under the v2 vocabulary.

Update this file (or create `0009_retag_ali_certs_v2.sql`):

```sql
-- Migration: Retag ALI certification rows under v2 3-tier vocabulary
-- These are public-safe industry reference data
UPDATE knowledge_items
SET category = ARRAY['tier-1-public']
WHERE source_type = 'ali_certification';
```

Run this against Neon during this PR (it's idempotent and fast).

### File 6: `README.md` and `bidiq/INGEST.md`

Update vocabulary references. Remove the 56-category language, add brief description of the 3-tier model with pointer to `docs/classifier-system-prompt-v2.md` for full detail.

### File 7: `lib/db.ts` and `scripts/setup-db.mjs`

No schema changes needed — TEXT[] still works fine. Just verify the field comment/description references the new model if any inline docs mention "56 categories".

---

## What does NOT change

- Database schema (TEXT[] still right; just storing 3 possible values now instead of 57)
- GIN index (still useful for ANY queries)
- Migration files 0007, 0010 (schema swap + CHECK constraint — both still valid)
- /api/ask retrieval logic (filters work the same with `&&` overlap and `ANY()` patterns)
- MCP tool definitions (`get_brand_info` category_breakdown still works)
- Two-tier ingester (Tier-1 vs Tier-2 PDF extraction is unrelated to classifier tiers — confusing naming, but they're different concepts)

The schema migration from PR #15 stays. We just put a different (much smaller) set of values into the same column.

---

## Verification steps

After the file changes:

1. **Syntax / type check:**
   ```
   python3 -m py_compile bidiq/ingest.py
   npx tsc --noEmit
   npx next build
   ```

2. **Module load test:**
   ```python
   from bidiq.ingest import CLASSIFIER_SYSTEM_PROMPT_V2, VALID_CATEGORIES
   assert "tier-1-public" in VALID_CATEGORIES
   assert "tier-2-internal" in VALID_CATEGORIES
   assert "tier-3-paul-only" in VALID_CATEGORIES
   assert "uncategorized" in VALID_CATEGORIES
   assert len(VALID_CATEGORIES) == 4
   assert len(CLASSIFIER_SYSTEM_PROMPT_V2) > 1000
   assert len(CLASSIFIER_SYSTEM_PROMPT_V2) < 10000  # confirming we're way under v1's 37k
   print("OK")
   ```

3. **Sample classification (manual test, optional):**
   Pick 3 sample PDFs from the 32 brand folders or from the Phase 2 staging output. Run them through `classify_document()` interactively. Verify the output is exactly one of the 4 valid tier values.

4. **Run 0009_retag_ali_certs_v2.sql against Neon.** Verify with:
   ```sql
   SELECT category, COUNT(*) FROM knowledge_items 
   WHERE source_type = 'ali_certification' 
   GROUP BY category;
   ```
   Should return one row: `{tier-1-public}` with count 3058.

---

## What I did NOT include in this brief (intentional)

- **Bulk re-tagging the 272 ingested_pdf rows.** Deferred — we'll do it in waves keyed to specific agent ingest needs. Running the retag script now would cost tokens and produce results we won't use until the agents come online.
- **Metadata extraction (entity, dollar_amount, date, contract_vehicle).** Deferred to Phase 2 enhancement. The 3-tier model is sufficient to ship the content engine and email agent. Metadata fields would require schema changes (new columns) which are out of scope for this PR.
- **Agent-tier enforcement at the API level.** The lib/category-tiers.ts constants document intent but aren't enforced in /api/ask today. Enforcement happens when each agent is built (email agent uses EMAIL_AGENT_EXCLUDE, content engine uses CONTENT_ENGINE_ALLOW).

---

## Coordination

- **Don't merge until Paul confirms.** Schema migration is fine to run; PR sits open for Paul's review.
- **Branch name:** `claude/classifier-v2-3tier`
- **PR title:** "Replace classifier with 3-tier access model (v1 → v2)"
- **PR description:** Summarize the change. Note that re-tagging existing rows is deferred. Note that this aligns with operating-manual sequence (KB enrichment first, then content engine architecture, then editorial calendar).

When ready, ping Paul: "Classifier v2 swap complete in PR #X. Ready for review. Existing rows untouched — retag deferred until corpus ingest waves."

---

## Why we're doing this

Quick context for the PR description:

The v1 classifier vocabulary had 56 distinct categories derived from Paul's voice/style guide scenarios. While well-intentioned, the taxonomy was over-engineered — it solved a librarian's problem (taxonomy completeness) rather than the actual problem (controlling what each agent can retrieve).

The 3-tier model directly maps to Liftnow's agent architecture:
- **Tier 1 (public)** — Content Engine produces public material; only sees tier-1
- **Tier 2 (internal)** — Email Agent and future Bid Tracker handle customer/operational context; see tier-1 + tier-2
- **Tier 3 (paul-only)** — Sensitive material (cost, financial, HR, M&A, legal) — never reaches any agent

This swap also aligns the bid-iq KB with the existing operating manual rather than introducing a parallel taxonomy. Per Paul: "I really don't want to deviate from the plan in liftnow-operating-manual.md."

Done.
