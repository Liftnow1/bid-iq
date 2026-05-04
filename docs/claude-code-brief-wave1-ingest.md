# Claude Code Brief: Wave 1 KB Ingest — 12 Carry Brand Folders

**Audience:** Claude Code
**Repo:** github.com/Liftnow1/bid-iq
**Branch:** `claude/wave1-carry-brand-ingest`
**Estimated runtime:** 2-4 hours
**Estimated cost:** $20-30 in Claude API calls
**Estimated rows added:** 1,800-2,200 in `knowledge_items` table

---

## TL;DR

Run the bid-iq ingester (`bidiq/ingest.py`, freshly merged with classifier v2 3-tier vocabulary) against 12 specific brand folders under `data/product_data/`. Skip everything else. Use the v2 classifier to tag each ingested PDF with one of `tier-1-public`, `tier-2-internal`, `tier-3-paul-only`, or `uncategorized`. Most should land in `tier-1-public`.

This is a CURATED ingest — Paul has already deduped and populated these folders. Do NOT attempt to re-classify or filter beyond what `bidiq/ingest.py` does natively.

---

## Pre-flight checks (RUN FIRST, halt if any fail)

### 1. Verify branch and main parity

```bash
cd /path/to/bid-iq
git checkout main
git pull origin main
git checkout -b claude/wave1-carry-brand-ingest
```

Confirm `main` contains commit `5ca3b42` ("Refresh docs + comments to reference v2 3-tier vocabulary") or later. If not, the merge didn't propagate — STOP and notify Paul.

### 2. Verify classifier v2 prompt loads

```bash
python -c "from bidiq.ingest import _load_classifier_prompt; p = _load_classifier_prompt(); print('OK', len(p), 'chars')"
```

Should print `OK <some-number> chars`. If it raises an exception, the prompt path is wrong and ingestion will fail downstream — STOP.

### 3. Verify Neon DB connection

```bash
python -c "from bidiq.db import get_connection; conn = get_connection(); cur = conn.cursor(); cur.execute('SELECT count(*) FROM knowledge_items'); print('Current row count:', cur.fetchone()[0])"
```

Should print current `knowledge_items` row count (expect ~3,330 = 272 ingested_pdf + 3,058 ali_certification + ~0 other). Capture this number — we'll compare post-ingest. If connection fails, env vars are wrong — STOP.

### 4. Verify ALI cert retag worked

```bash
python -c "from bidiq.db import get_connection; conn = get_connection(); cur = conn.cursor(); cur.execute(\"SELECT category, count(*) FROM knowledge_items WHERE source_type='ali_certification' GROUP BY category\"); print(cur.fetchall())"
```

Should print exactly one row: `[('{tier-1-public}', 3058)]` (or similar — count may vary). If it shows old vocab like `{specifications, industry-reference}`, the SQL migration didn't run — STOP.

### 5. Inspect `bidiq/ingest.py` CLI

Read the file and identify the CLI entry point. Most likely either:

- A `__main__` block that takes `--source <path>` arguments
- A function like `ingest_directory(path, **kwargs)` that's importable

Document the exact invocation pattern in your scratch notes — we'll use it for the per-brand loop.

### 6. Confirm carry brand list against `brands` table

```bash
python -c "from bidiq.db import get_connection; conn = get_connection(); cur = conn.cursor(); cur.execute(\"SELECT name FROM brands WHERE we_carry=TRUE ORDER BY name\"); print([r[0] for r in cur.fetchall()])"
```

Expected output (12 names — `liftnow` may also be in we_carry=TRUE but we explicitly skip it for Wave 1):

```
['alemite', 'balcrank', 'bendpak', 'champion', 'challenger', 'coats',
 'lincoln', 'mahle', 'mattei', 'omer', 'pks', 'pro-cut']
```

If the DB returns different names (different casing, missing brands, extras), reconcile against this canonical list before proceeding. STOP if there's a mismatch you can't resolve.

---

## What to ingest

### Process these 12 brand folders (and ONLY these):

```
data/product_data/alemite/
data/product_data/balcrank/
data/product_data/bendpak/
data/product_data/champion/
data/product_data/challenger/
data/product_data/coats/
data/product_data/lincoln/
data/product_data/mahle/
data/product_data/mattei/
data/product_data/omer/
data/product_data/pks/
data/product_data/pro-cut/
```

### Hard skip rules — DO NOT ingest:

**Folders to skip entirely:**
- `data/product_data/liftnow/` (deferred per Paul)
- `data/product_data/hunter/`, `data/product_data/mohawk/`, `data/product_data/rotary/`, `data/product_data/nussbaum/`, `data/product_data/stertil-koni/`, `data/product_data/ari-hetra/` (competitor brands — separate workstream, parked)
- `data/product_data/atlas/`, `atlas-copco/`, `forward-lift/`, `gardner-denver/`, `gray/`, `ingersoll-rand/`, `kaeser/`, `sefac/`, `snap-on/`, `whip/`, `cps/`, `robinair/`, `cemb/` (empty or non-carry, not in scope)

**Subfolders to skip in any brand:**
- `**/_DUPLICATES/` (deduped quarantine — never ingest these)
- `**/_DEDUP-REPORT.csv`, `**/_DEDUP-EXECUTION-LOG.csv` (audit artifacts, not docs)

**File types to skip:**
- Anything that's not `.pdf`
- Files starting with `.` (system files)
- Files starting with `_` (administrative artifacts like `_DEDUP-REPORT.csv`)

### Notable file to flag

`data/product_data/balcrank/` contains a single 70-80 MB Balcrank catalog PDF. Ingestion will work but PDF text extraction for this file may take 30-90 seconds (vs <5 sec for typical files). DO NOT timeout-fail it. Allow at least 5 minutes for any single file before declaring it stuck.

---

## Ingestion sequence

### Per-brand processing (sequential, NOT parallel)

For each of the 12 carry brands, in this order (smallest first, largest last — fail fast on small brands if something's broken):

1. **PKS** (~30 PDFs) — small test pass, runs first
2. **Mattei** (~20-40 PDFs) — recently populated by Paul
3. **Mahle** (~50 PDFs)
4. **Lincoln** (~30-50 PDFs) — recently populated by Paul
5. **Omer** (~77 PDFs)
6. **Pro-cut** (~78 PDFs)
7. **Challenger** (~89 PDFs)
8. **Coats** (~116 PDFs)
9. **Balcrank** (~202 PDFs, includes the 70-80 MB catalog)
10. **BendPak** (~387 PDFs)
11. **Alemite** (~390 PDFs)
12. **Champion** (~842 PDFs, deduped) — biggest, last

### After each brand:

Run a short summary query:

```sql
SELECT category, count(*)::int AS n
  FROM knowledge_items
 WHERE source_type = 'ingested_pdf'
   AND brand_id = (SELECT id FROM brands WHERE name = '<brand>')
   AND created_at > '<ingest-start-timestamp>'
 GROUP BY category;
```

Print the breakdown. Expect distribution roughly like:
- `{tier-1-public}`: 90%+
- `{uncategorized}`: 5-10% (corrupted PDFs, blank pages, weird encoding)
- `{tier-2-internal}`: rare (would indicate operational doc misfiled in product folder — flag for Paul)
- `{tier-3-paul-only}`: should be ZERO (if any appear, STOP and ask Paul — these would be misplaced files)

If any brand shows >5% non-tier-1 results, log it but continue. If >25% non-tier-1, STOP — the classifier is misbehaving for that brand.

---

## Stop-and-confirm checkpoints

Halt and ask Paul before proceeding past these gates:

### Checkpoint 1: After PKS (the first brand)

Report:
- Total PDFs scanned in PKS folder
- Total ingested successfully
- Tier distribution
- Cost so far (estimated from API call count)
- Sample of 5 random newly-ingested rows (filename + tier + first 100 chars of search_text)

Wait for Paul's "go" before proceeding to brand 2.

### Checkpoint 2: After 4 brands (PKS, Mattei, Mahle, Lincoln)

Same metrics. Wait for Paul's "go" before proceeding.

### Checkpoint 3: After 8 brands (through Coats)

Same metrics. Wait for Paul's "go" before the big brands (Balcrank, BendPak, Alemite, Champion).

### Checkpoint 4: Final (after Champion)

Comprehensive summary — see "Final report" section below.

---

## Error handling

### Per-file errors (log and continue)

- **Corrupted/unreadable PDF:** Log `<filename>: corrupted` and skip. Do NOT halt.
- **Empty/blank PDF (no extractable text):** Tag as `uncategorized`, ingest with empty `search_text`. Don't skip — Paul wants visibility into what's there.
- **Classifier returns invalid tag:** Default to `['uncategorized']`. Log the invalid response.
- **Single DB write failure:** Retry once with exponential backoff. If second attempt fails, log and skip.

### Fatal errors (STOP)

- **5 consecutive PDF processing failures:** Something systemic is broken. STOP and ask Paul.
- **DB connection lost mid-run:** Try once to reconnect. If still down, STOP.
- **Anthropic API rate limit hit and not recovering after 60 sec backoff:** STOP and ask Paul.
- **Disk write errors:** STOP immediately.

### Cost overrun guardrail

If estimated cost exceeds $50 (5x expected), STOP and ask Paul before continuing. This catches runaway loops or unexpectedly token-heavy PDFs.

---

## Logging

Write a structured log to `logs/wave1-ingest-<timestamp>.csv` with one row per PDF:

| Column | Description |
|---|---|
| `timestamp` | ISO 8601 |
| `brand` | Brand folder name |
| `filename` | PDF filename |
| `file_size_mb` | File size |
| `pages` | Total page count |
| `extraction_chars` | Chars extracted into search_text |
| `category_assigned` | What classifier returned |
| `status` | `ingested` / `skipped-corrupt` / `skipped-empty` / `failed` |
| `error_message` | If applicable |
| `db_row_id` | knowledge_items.id if ingested |
| `api_cost_estimate` | Approximate API cost for this row |

Also keep a running summary in `logs/wave1-summary.json` updated after each brand:

```json
{
  "started_at": "...",
  "current_brand": "champion",
  "brands_completed": ["pks", "mattei", "mahle", ...],
  "total_files_processed": 1234,
  "total_files_ingested": 1180,
  "total_files_skipped": 54,
  "tier_distribution": {"tier-1-public": 1100, "uncategorized": 80, ...},
  "estimated_cost_usd": 18.42,
  "elapsed_minutes": 78
}
```

---

## Final report

After Champion completes (or after fatal halt), produce `logs/wave1-final-report.md`:

```markdown
# Wave 1 KB Ingest — Final Report

**Started:** <ISO>
**Completed:** <ISO>
**Total runtime:** <minutes>
**Total cost (estimated):** $<X.XX>

## Per-brand summary

| Brand | PDFs scanned | Ingested | Skipped | Tier-1-public | Uncategorized | Other |
|---|---|---|---|---|---|---|
| pks | ... | ... | ... | ... | ... | ... |
| ... | ... | ... | ... | ... | ... | ... |

## Tier distribution (totals)

- tier-1-public: X
- tier-2-internal: X (LIST FILENAMES — these need Paul's review)
- tier-3-paul-only: X (LIST FILENAMES — flag for Paul, should be 0)
- uncategorized: X

## Knowledge items table state

- Pre-ingest row count: <N>
- Post-ingest row count: <N>
- Net new rows: <N>

## Files needing Paul's attention

- All tier-2 and tier-3 ingested files (filename + brand)
- Any files where extraction returned <500 chars (probably broken)
- Any files where the classifier returned an invalid tag

## Verification queries Paul should run

(provide 3-5 SQL queries Paul can run in Neon to spot-check the ingest)
```

Provide this file to Paul, plus a summary in chat.

---

## What's explicitly OUT OF SCOPE for Wave 1

DO NOT do any of these:

1. **Re-tag the 272 legacy `ingested_pdf` rows.** They have old 56-category vocab. Deferred to a separate cleanup task post-Wave-1.
2. **Ingest Pillar 3 docs** (capability statements, voice guide, industry references). Separate workstream after Wave 1 verifies.
3. **Push PDFs to git.** They're gitignored. PDFs are local-only by design.
4. **Process competitor brands** (Hunter, Mohawk, Rotary, etc.). Parked workstream.
5. **Process the Liftnow-branded folder.** Even if populated, skip.
6. **Build a technical_kb/ structure** (drawings, schematics, exploded views split). That's a future architectural change Paul may decide later.
7. **Modify the classifier prompt** to handle edge cases. If the prompt produces bad results, log them — don't try to fix the prompt mid-ingest.
8. **Run any tier-2 deep extraction.** Wave 1 is shallow only. Deep extraction happens lazily at query time.
9. **Push the new branch to remote.** This is a local-only ingest run. Logs go to `logs/` (gitignored).
10. **Make schema changes.** Schema is locked. Use it as-is.

---

## What success looks like

After Wave 1 completes:

1. `knowledge_items` table grew by ~1,800-2,200 rows
2. The vast majority of new rows are tagged `{tier-1-public}`
3. Per-brand counts roughly match expected (Champion has the most ingested, PKS has the least)
4. `bid-iq-neon.vercel.app/api/ask` works — try a content question like "What are the install requirements for the Challenger 4018?" and verify it pulls from carry-brand sources
5. No tier-3 entries (or all flagged for Paul if any)
6. Total cost under $35
7. Detailed log + final report exist for Paul to review

When you've completed Wave 1 and have the final report ready, ping Paul with:

> Wave 1 complete. <N> files ingested across 12 carry brands. Tier-1-public: <N>. Uncategorized: <N>. Cost: $<X>. Final report at logs/wave1-final-report.md. Recommend Paul spot-check 10 random rows before greenlighting Pillar 3 ingest.

That's it. Begin with the pre-flight checks. Halt at any of the four checkpoints for Paul's approval. Don't proceed past Champion without delivering the final report.
