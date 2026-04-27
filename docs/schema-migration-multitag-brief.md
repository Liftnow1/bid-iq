# Bid-iq Schema Migration Brief: Multi-Tag Categories (TEXT → TEXT[])

**Version:** 1.0 | **Audience:** Claude Code | **Date:** 2026-04-27 | **Repo:** github.com/Liftnow1/bid-iq

---

## TL;DR

Migrate the `knowledge_items.category` column from a single TEXT value to a TEXT[] array. Replace the existing 10-category vocabulary with the new 56-category vocabulary v4-trimmed. Update the ingester to assign multiple tags. Update `/api/ask` retrieval to use array-aware filtering. Update MCP tools to expose array tags. Migrate the existing 272 ingested_pdf rows by wrapping their single category into an array.

This is a backwards-incompatible schema change but the data migration is straightforward (single value → single-element array, then re-tag with new vocabulary).

**Do this BEFORE bulk Tier-1 ingestion across the brand corpus.**

---

## Background Context

Bid-iq currently stores a single category per document:

```sql
-- Current schema
CREATE TABLE knowledge_items (
  id SERIAL PRIMARY KEY,
  brand_id INTEGER REFERENCES brands(id),
  source_type TEXT NOT NULL CHECK (source_type IN ('ingested_pdf', 'ali_certification')),
  category TEXT,  -- ← single value, will become TEXT[]
  search_text TEXT,
  raw_content TEXT,
  -- other columns...
);
```

The current 10-category vocabulary (`installation-guides`, `service-procedures`, etc.) is being replaced with a 56-category vocabulary v4-trimmed. See `/mnt/user-data/outputs/liftnow-kb-classifier-system-prompt-v1.md` for the full vocabulary and decision rules.

**Why multi-tag:**
- Manufacturer IOMs typically cover install + operation + service + parts in one document. Single-tag forces a lossy choice.
- Bid responses that include capability + pricing + compliance forms genuinely span multiple categories.
- Retrieval is much better when documents can match multiple query intents.

---

## Migration Steps

### Step 1: Schema Migration

Create a migration file `migrations/0007_multitag_categories.sql`:

```sql
-- Migration: Convert category from TEXT to TEXT[]
-- Date: 2026-04-27

BEGIN;

-- Step 1a: Add new column
ALTER TABLE knowledge_items 
  ADD COLUMN category_new TEXT[];

-- Step 1b: Migrate existing data — wrap single value in array
UPDATE knowledge_items 
  SET category_new = ARRAY[category] 
  WHERE category IS NOT NULL AND category != '';

UPDATE knowledge_items
  SET category_new = ARRAY[]::TEXT[]
  WHERE category IS NULL OR category = '';

-- Step 1c: Drop old column, rename new
ALTER TABLE knowledge_items DROP COLUMN category;
ALTER TABLE knowledge_items RENAME COLUMN category_new TO category;

-- Step 1d: Add GIN index for array containment queries (much faster than seq scan)
CREATE INDEX idx_knowledge_items_category_gin ON knowledge_items USING GIN (category);

-- Step 1e: Add a constraint to ensure at least one category (later, after re-tagging)
-- ALTER TABLE knowledge_items ADD CONSTRAINT knowledge_items_category_not_empty 
--   CHECK (array_length(category, 1) >= 1);
-- Note: leave commented for now. Apply after Tier-1 re-tagging is complete.

COMMIT;
```

**Run command:**
```bash
psql "$DATABASE_URL" -f migrations/0007_multitag_categories.sql
```

**Verification queries:**
```sql
-- Should return 'ARRAY' as the data type
SELECT data_type FROM information_schema.columns 
WHERE table_name='knowledge_items' AND column_name='category';

-- Should match pre-migration row count (272 ingested_pdf + 3058 ali_certification)
SELECT count(*), source_type FROM knowledge_items GROUP BY source_type;

-- Sample row to verify wrapping worked
SELECT id, category, source_type FROM knowledge_items 
WHERE source_type = 'ingested_pdf' LIMIT 5;
-- Expected output: category is `{installation-guides}` (array with one element), not `installation-guides` (scalar)
```

### Step 2: Update the Ingester (`bidiq/ingest.py` or equivalent)

**Find the classification call** in the ingester. It currently looks something like:

```python
# OLD (single tag)
classification_response = anthropic_client.messages.create(
    model="claude-sonnet-4-5",
    system=CLASSIFIER_SYSTEM_PROMPT,
    messages=[{"role": "user", "content": document_text}]
)
category = classification_response.content[0].text.strip()
# Then INSERT INTO knowledge_items (..., category, ...) VALUES (..., %s, ...)
```

**Replace with multi-tag version:**

```python
# NEW (multi-tag)
import json

classification_response = anthropic_client.messages.create(
    model="claude-sonnet-4-5",
    system=CLASSIFIER_SYSTEM_PROMPT_V1,  # Load from liftnow-kb-classifier-system-prompt-v1.md
    messages=[{"role": "user", "content": document_text}]
)

raw_response = classification_response.content[0].text.strip()

# Parse JSON array response
try:
    categories = json.loads(raw_response)
    if not isinstance(categories, list) or not categories:
        categories = ["uncategorized"]
except json.JSONDecodeError:
    # Fallback for non-JSON responses
    categories = ["uncategorized"]

# Validate against controlled vocabulary
VALID_CATEGORIES = {
    "installation-guides", "service-procedures", "parts-catalog", "specifications",
    "operation-manuals", "safety-warnings", "warranty-documentation", "marketing-brochure",
    "manufacturer-training", "technical-bulletin", "site-survey", "compliance-regulations",
    "procurement-process", "industry-reference", "rfp-received", "compliance-template",
    "install-handoff-sop", "service-workflow-sop", "contract-reporting-sop", "sales-playbook",
    "capability-statement", "cold-outreach-template", "voice-samples", "sales-collateral",
    "case-study", "liftnow-internal-training", "liftnow-credentials", "insurance-policy",
    "bond-instrument", "rfp-response", "customer-quote-history", "customer-po",
    "customer-invoice", "customer-contract", "customer-account-setup", "vendor-onboarding-completed",
    "vendor-po", "vendor-invoice", "vendor-agreement", "subcontract-agreement",
    "vendor-cost-pricing", "list-pricing", "service-record", "install-record",
    "payment-record", "damage-claim", "certified-payroll", "contract-reporting-record",
    "bid-protest", "change-order", "competitive-intelligence", "win-loss-debrief",
    "financial-statement", "commission-report", "employment-document", "regulatory-update",
    "uncategorized"
}

# Filter to valid categories only
categories = [c for c in categories if c in VALID_CATEGORIES]
if not categories:
    categories = ["uncategorized"]

# Insert with TEXT[] format
cursor.execute(
    """
    INSERT INTO knowledge_items (brand_id, source_type, category, search_text, raw_content, ...)
    VALUES (%s, %s, %s, %s, %s, ...)
    """,
    (brand_id, source_type, categories, search_text, raw_content, ...)
)
```

**Critical: psycopg2 / asyncpg automatically converts Python lists to PostgreSQL arrays.** No need to manually format `{a,b,c}` — just pass a Python list.

**The system prompt to load:** Read `/mnt/user-data/outputs/liftnow-kb-classifier-system-prompt-v1.md` and embed its content (Parts 1-8 plus Appendix A) as the `CLASSIFIER_SYSTEM_PROMPT_V1` constant.

### Step 3: Update `/api/ask` Retrieval

The `/api/ask` endpoint needs to filter by category arrays. Common patterns:

```python
# OLD: filter by single category
WHERE category = $1

# NEW: filter by category presence in array (any match)
WHERE $1 = ANY(category)

# NEW: filter by any of multiple categories (overlap)
WHERE category && $1::TEXT[]

# NEW: filter by all required categories present
WHERE category @> $1::TEXT[]

# NEW: exclude categories (DENY list)
WHERE NOT (category && $1::TEXT[])
```

**Update the retrieval query** to use these patterns. The most common need will be `category && $1::TEXT[]` (overlap) for "show me anything tagged with any of these categories."

**Update SOURCE_TYPE_PDF_BOOST logic** — this should still work since the boost is on `source_type`, not `category`. No change needed there.

**Add agent-tier filtering** (future-ready, not strictly needed for `/api/ask` which is bid-agent-equivalent):

```python
# For future bid agent (sees almost everything)
PAUL_ONLY = ['financial-statement', 'commission-report', 'employment-document']
ALWAYS_EXCLUDE_FOR_BID_AGENT = PAUL_ONLY

# For future email agent
EMAIL_AGENT_EXCLUDE = PAUL_ONLY + [
    'vendor-cost-pricing', 'customer-invoice', 'insurance-policy', 'bond-instrument',
    'vendor-po', 'vendor-invoice', 'payment-record', 'certified-payroll',
    'contract-reporting-record', 'bid-protest', 'change-order',
    'competitive-intelligence', 'win-loss-debrief'
]

# For future content engine (allow list)
CONTENT_ENGINE_ALLOW = [
    'installation-guides', 'service-procedures', 'parts-catalog', 'specifications',
    'operation-manuals', 'safety-warnings', 'warranty-documentation', 'marketing-brochure',
    'manufacturer-training', 'technical-bulletin', 'site-survey', 'compliance-regulations',
    'procurement-process', 'industry-reference', 'capability-statement', 'sales-collateral',
    'case-study', 'list-pricing', 'regulatory-update', 'customer-quote-history',
    'voice-samples'
]
```

For now, don't enforce these in `/api/ask` (Paul is the only user via MCP). Document these in the codebase as constants for future use.

### Step 4: Update MCP Tools

The MCP server at `/api/mcp` exposes three tools: `ask_bidiq`, `list_brands`, `get_brand_info`.

**`ask_bidiq` doesn't need changes** — it queries through `/api/ask` which we're updating in Step 3.

**`list_brands` doesn't need changes** — it's about brands, not documents.

**`get_brand_info` may benefit from showing category breakdown** — when a user asks for info on a brand (e.g., Challenger), it would be useful to show:
- Total documents: N
- Document categories represented: [list of categories with counts]

Update the response to include category aggregation:

```python
# In get_brand_info handler
cursor.execute(
    """
    SELECT unnest(category) as cat, count(*) as cnt
    FROM knowledge_items
    WHERE brand_id = $1
    GROUP BY cat
    ORDER BY cnt DESC
    """,
    (brand_id,)
)
category_counts = cursor.fetchall()

response['document_categories'] = [
    {'category': row['cat'], 'count': row['cnt']}
    for row in category_counts
]
```

### Step 5: Re-Tag Existing 272 ingested_pdf Rows

The 272 existing PDFs were tagged with the OLD 10-category vocabulary. They now need re-classification with the new 56-category vocabulary v4-trimmed.

**Create a re-classification script** `scripts/retag_existing_documents.py`:

```python
"""
Re-classifies all ingested_pdf rows using the new v4-trimmed classifier prompt.
Run after schema migration is complete.
"""

import os
import json
import psycopg2
from anthropic import Anthropic

DATABASE_URL = os.environ['DATABASE_URL']
ANTHROPIC_API_KEY = os.environ['ANTHROPIC_API_KEY']

# Load classifier system prompt from file
with open('docs/liftnow-kb-classifier-system-prompt-v1.md', 'r') as f:
    CLASSIFIER_PROMPT = f.read()

VALID_CATEGORIES = {  # ... full list from Step 2 ... }

def main():
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    cur.execute("""
        SELECT id, search_text, raw_content 
        FROM knowledge_items 
        WHERE source_type = 'ingested_pdf'
        ORDER BY id
    """)
    rows = cur.fetchall()
    
    print(f"Re-tagging {len(rows)} ingested_pdf documents...")
    
    for i, (doc_id, search_text, raw_content) in enumerate(rows, 1):
        # Use raw_content if available (full doc), else search_text
        doc_text = raw_content if raw_content else search_text
        if not doc_text:
            continue
        
        # Truncate if very long (classifier doesn't need full content)
        if len(doc_text) > 50000:
            doc_text = doc_text[:25000] + "\n\n[...truncated...]\n\n" + doc_text[-25000:]
        
        try:
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=200,
                system=CLASSIFIER_PROMPT,
                messages=[{"role": "user", "content": doc_text}]
            )
            
            raw = response.content[0].text.strip()
            categories = json.loads(raw)
            
            if not isinstance(categories, list) or not categories:
                categories = ["uncategorized"]
            
            categories = [c for c in categories if c in VALID_CATEGORIES]
            if not categories:
                categories = ["uncategorized"]
            
            cur.execute(
                "UPDATE knowledge_items SET category = %s WHERE id = %s",
                (categories, doc_id)
            )
            conn.commit()
            
            print(f"[{i}/{len(rows)}] Doc {doc_id}: {categories}")
        
        except Exception as e:
            print(f"[{i}/{len(rows)}] Doc {doc_id}: ERROR — {e}")
            conn.rollback()
            continue
    
    conn.close()
    print("Done.")

if __name__ == '__main__':
    main()
```

**Run command:**
```bash
python scripts/retag_existing_documents.py
```

**Cost estimate:** 272 docs × ~$0.005/classification with Sonnet = ~$1.40 total. Cheap.

**Verification after re-tag:**
```sql
-- Distribution of categories after re-tag
SELECT unnest(category) as cat, count(*) as cnt
FROM knowledge_items
WHERE source_type = 'ingested_pdf'
GROUP BY cat
ORDER BY cnt DESC;

-- How many docs got multi-tagged?
SELECT array_length(category, 1) as n_tags, count(*) as cnt
FROM knowledge_items
WHERE source_type = 'ingested_pdf'
GROUP BY n_tags
ORDER BY n_tags;
```

Expect a meaningful number of docs to have 2-4 tags. If everything has 1 tag, the classifier prompt isn't working as intended (multi-tag heuristics not being applied).

### Step 6: Re-Tag the 3,058 ali_certification Rows

These are catalog certification entries from the ALI registry. They're NOT really "documents" — they're metadata records. They should keep `source_type = 'ali_certification'` but get appropriate category tags.

**Most ALI cert entries should tag as `specifications`** (since they're product compliance specs).

**Bulk update SQL** (no need for AI classification since they're all the same type):

```sql
UPDATE knowledge_items
SET category = ARRAY['specifications', 'industry-reference']
WHERE source_type = 'ali_certification'
AND (category IS NULL OR category = ARRAY[]::TEXT[]);
```

**Why two tags:** ALI certs are both technical specs (specifications) and industry reference material (the ALI registry being a trade reference). This dual-tag improves retrieval for both spec-focused queries and reference-focused queries.

### Step 7: Update Documentation

Update these repo docs:

1. **`README.md`** — Note the new 56-category vocabulary, link to classifier system prompt.
2. **`docs/schema.md`** (or equivalent) — Document the TEXT[] migration.
3. **`docs/api.md`** — Document the array-filtering query patterns for `/api/ask`.
4. **Save the classifier prompt to repo:** `docs/classifier-system-prompt-v1.md`

### Step 8: Add Constraint (Final Step)

After all 272 + 3,058 rows are re-tagged and verified non-empty, enable the not-empty constraint:

```sql
ALTER TABLE knowledge_items 
  ADD CONSTRAINT knowledge_items_category_not_empty 
  CHECK (array_length(category, 1) >= 1);
```

This prevents future bugs from inserting docs with empty category arrays.

---

## Test Plan

After completing Steps 1-8:

### Test 1: Schema is correct
```sql
\d knowledge_items
-- category column should show as `text[]` not `text`
```

### Test 2: GIN index exists
```sql
\di knowledge_items*
-- Should see idx_knowledge_items_category_gin
```

### Test 3: Sample query — array containment
```sql
-- Find all docs that include 'specifications' as one of their tags
SELECT id, category, source_type 
FROM knowledge_items 
WHERE 'specifications' = ANY(category) 
LIMIT 10;
```

### Test 4: Sample query — overlap with multiple categories
```sql
-- Find all docs tagged with any of these
SELECT id, category, source_type 
FROM knowledge_items 
WHERE category && ARRAY['installation-guides', 'service-procedures']
LIMIT 10;
```

### Test 5: /api/ask returns results
- Query: "How do I install the Challenger 4018?"
- Should return docs tagged with `installation-guides` (and likely also `operation-manuals`, `service-procedures`)
- Verify the cited sources include array-tagged documents

### Test 6: MCP tool works
From Claude Code:
```
claude> /mcp
# Verify bidiq is connected

claude> Ask bidiq about Coats Maxx80 service procedures
# Should return service-procedures-tagged documents
```

### Test 7: Multi-tag distribution looks reasonable
```sql
-- Should see meaningful distribution of 1-tag, 2-tag, 3-tag, 4-tag docs
SELECT array_length(category, 1) as n_tags, count(*) 
FROM knowledge_items 
WHERE source_type = 'ingested_pdf' 
GROUP BY n_tags;
```

Expected: Most docs 2-3 tags, some 1, some 4+.

### Test 8: No invalid categories
```sql
-- Should return 0 rows
SELECT id, category 
FROM knowledge_items
WHERE category && ARRAY['random-invalid-tag', 'made-up-category'];
```

---

## Rollback Plan

If something goes catastrophically wrong:

```sql
BEGIN;

-- Restore old single-value column
ALTER TABLE knowledge_items ADD COLUMN category_old TEXT;

-- Take first element of array
UPDATE knowledge_items SET category_old = category[1];

-- Drop the array column
ALTER TABLE knowledge_items DROP COLUMN category;
ALTER TABLE knowledge_items RENAME COLUMN category_old TO category;

DROP INDEX IF EXISTS idx_knowledge_items_category_gin;

COMMIT;
```

This loses multi-tag information but restores the old schema. Combine with restoring the old ingester code from git.

---

## Open Questions / Coordination Points

**Question for Paul:** Do you want the re-tagging in Step 5 to happen before or after we start ingesting fresh content with the new vocabulary? I recommend BEFORE — clean slate first, then ingest at scale.

**Question for Paul:** Should the constraint in Step 8 be applied immediately or held for later? I recommend immediately after Steps 5-6 verify everything is non-empty.

**Coordination with email agent:** The email agent's voice guide loading is unaffected (still loads the .md from OneDrive). But if/when the email agent starts pulling from bid-iq, it will use the array-filter query patterns documented in Step 3.

---

## Estimated Effort and Cost

| Step | Effort | Cost |
|------|--------|------|
| 1. Schema migration | 5 min | $0 |
| 2. Update ingester | 30-60 min | $0 |
| 3. Update /api/ask | 30 min | $0 |
| 4. Update MCP tools | 15 min | $0 |
| 5. Re-tag 272 PDFs | 30 min runtime | ~$1.50 |
| 6. Tag 3,058 ALI rows | 1 SQL command | $0 |
| 7. Update docs | 30 min | $0 |
| 8. Add constraint | 1 SQL command | $0 |
| **Total** | **~3 hours active work** | **~$1.50** |

---

## Success Criteria

Migration is complete when:

- [ ] `knowledge_items.category` is TEXT[] type
- [ ] GIN index exists on the column
- [ ] All 272 ingested_pdf rows have non-empty category arrays with valid tags
- [ ] All 3,058 ali_certification rows tagged with `['specifications', 'industry-reference']`
- [ ] Ingester writes new documents with multi-tag arrays
- [ ] `/api/ask` returns results when filtering by single tag, multiple tags (overlap), or with deny lists
- [ ] MCP `get_brand_info` shows category distribution
- [ ] Test query "How do I install Challenger 4018?" returns multiple relevant docs
- [ ] Constraint prevents empty arrays
- [ ] Documentation updated and committed
- [ ] PR merged to main

When all checked, brief Paul that the schema is locked and we're ready for bulk Tier-1 ingestion across the brand corpus.
