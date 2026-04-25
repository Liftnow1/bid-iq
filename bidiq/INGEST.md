# bidiq PDF ingestion

Two Python tools live in `bidiq/`. They are NOT interchangeable.

| Tool | Scope | Output | Status |
| --- | --- | --- | --- |
| `bidiq/enrich.py` | Mohawk-only installation drawings and spec PDFs | JSON files in `kb_output/` | Historical / legacy. Do not extend. |
| `bidiq/ingest.py` | Any brand, any document type under `data/product_data/<brand>/` | Rows in the Postgres `knowledge_items` table | Active. Use this for new work. |

## Two-tier model

`ingest.py` writes into a single `knowledge_items` table but in two distinct tiers:

| Tier | Pages sent to Claude | `raw_content` | `extractor_version` | Cost / PDF | When to run |
| --- | --- | --- | --- | --- | --- |
| **1 — shallow** | first + last page only | `NULL` | `ingest.py-v1-tier1` | ~$0.01–$0.03 | Eager, run across the whole corpus once |
| **2 — deep** | every page (existing behavior) | full markdown body | `ingest.py-v1-tier2` | ~$0.10–$2.00 | Lazy, on-demand or for a specific brand under active bid |

A Tier-1 row is **upgraded in place** to Tier-2 by re-running on the same PDF — no row deletion, no second row. The row's id is stable across the upgrade.

A row is considered Tier-2 if EITHER `raw_content` is non-empty OR `extracted_data->>'tier' = '2'`. This second-chance check intentionally treats legacy `ingest.py-v1` rows (3324–3326 from the pre-tier era) as Tier-2 without any backfill.

## When to run what

- **First contact with a new brand folder** → `ingest.py --tier 1`. Cheap surface scan.
- **Brand actively in a bid** → `ingest.py --tier 2 --brand <name>`. Deep extraction.
- **A query in `/api/ask` is hitting unwarmed Tier-1 docs** → no action needed; `/api/ask` auto-upgrades the top-5 candidates synchronously on first query.
- **You are looking at `enrich.py`** → leave it alone. It is retained as a historical artifact.

## What `ingest.py` does (Tier 1)

1. Walks `data/product_data/` recursively (or just `data/product_data/<brand>/`).
2. For each PDF:
   - Skips if a row already exists at any tier (Tier-2 is a strict superset of Tier-1; never re-do or downgrade).
   - Reads the page count via `pdfinfo` (no rasterization).
   - Rasterizes ONLY page 1 and page N to JPEG.
   - Calls Claude vision with a shallow classification prompt — extracts `title`, `category`, `summary`, `tags`, `effective_date`, `supersedes_previous`.
   - UPSERTs a row with `raw_content = NULL`, `extracted_data.tier = 1`, `extractor_version = 'ingest.py-v1-tier1'`. `search_text` is `title + summary + tags`.

## What `ingest.py` does (Tier 2)

1. Same walk and brand-registry behavior.
2. For each PDF:
   - Skips if a Tier-2 row already exists AND `extracted_at` is newer than the PDF's mtime.
   - Rasterizes ALL pages.
   - Runs **one classification pass** (cover + last page) for `title`, `category`, `summary`, `tags`, `effective_date`, `supersedes_previous`.
   - Splits the page list into chunks of `--chunk-size` (default 5) and **fans out body extraction in parallel** (up to `--chunk-concurrency`, default 5). Each chunk returns just `content_markdown` for those pages.
   - Stitches the chunk markdown back together in page order to form the final `raw_content`.
   - Per-page descriptions (`pages_summary`) are off by default; pass `--include-page-summaries` to have each chunk also emit them.
   - UPSERTs (UPDATEs an existing Tier-1 row in place) with `raw_content` populated, `extracted_data.tier = 2`, `extractor_version = 'ingest.py-v1-tier2'`. `search_text` covers the **full** `raw_content` plus title, summary, and tags — no truncation. Postgres FTS handles multi-MB tsvectors fine, and truncating to 5 000 chars meant long install manuals only had ~6 % of their body indexed.

Connections are NOT held during the long Claude calls — the worker reads what it needs from Postgres, closes the connection, runs the vision pass, then opens a fresh connection to write the result. This keeps Neon from killing the transaction with `IdleInTransactionSessionTimeout` on long manuals.

On failure (either tier), a line is appended to `data/extraction-errors.log` and the run continues.

### Performance

Pre-chunking, a 75-page Tier-2 ingest spent ~10 minutes in a single Claude call. With `chunk_size=5` and `chunk_concurrency=5` (defaults) the same 75-page PDF runs as 15 chunks × 5 pages with up to 5 in flight at once — target wall-clock under 2 minutes. DPI default is **130**, the quality-vs-speed sweet spot for Claude vision; bump to 150–200 for very dense engineering drawings if needed.

## Schema notes

The build brief used a column named `content`; the actual schema uses `raw_content`. Tier-1 sets `raw_content = NULL`, so the column was relaxed to nullable in `lib/db.ts` / `scripts/setup-db.mjs`. Existing legacy rows (with `raw_content` already populated) are unaffected by the relaxation.

`extracted_data` JSONB carries: `tier` (1 or 2), `effective_date`, `supersedes_previous`, and (Tier 2 only, when `--include-page-summaries` was passed) `pages_summary`.

## CLI

```
bidiq kb ingest [OPTIONS]
```

Or directly: `python -m bidiq.ingest [OPTIONS]`.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--tier {1,2}` | `1` | Shallow vs deep. |
| `--brand <name>` | (none) | Restrict the walker to `data/product_data/<name>/` only. Folder names are lowercase (`challenger`, `mohawk`, `bendpak`, etc.). |
| `--id <int>` | (none) | Process a single `knowledge_items` row by id. The row's `source_path` is resolved to the on-disk PDF. Used by the `/api/knowledge-base/upgrade` endpoint. Mutually exclusive with `--brand`/`--limit` (those are ignored when `--id` is set). |
| `--limit <n>` | (none) | Only process the first N PDFs that are NOT already at the requested tier. Use for testing. |
| `--dry-run` | off | List what would be processed and skipped. No API calls, no DB writes. |
| `--concurrency <n>` | `3` | Parallel PDF workers (each holds its own DB connection + Anthropic client). |
| `--dpi <n>` | `130` | DPI passed to `pdf2image`. 130 is the quality / speed sweet spot for Claude vision. |
| `--model <id>` | `claude-sonnet-4-20250514` | Anthropic model. |
| `--chunk-size <n>` | `5` | **Tier 2 only.** Pages per parallel body-extraction chunk. Smaller = more parallelism but more API calls. |
| `--chunk-concurrency <n>` | `5` | **Tier 2 only.** Max in-flight chunk requests per PDF. Combined with `--concurrency`, total in-flight = `concurrency × chunk-concurrency`. |
| `--include-page-summaries` | off | **Tier 2 only.** Also emit per-page descriptions into `extracted_data.pages_summary`. The full body is in `raw_content` regardless; this is purely additive metadata. |

## Required env vars

- `DATABASE_URL` — Postgres connection string.
- `ANTHROPIC_API_KEY` — Anthropic API key.

Both required for real runs. `--dry-run` only needs `DATABASE_URL` if you want accurate "already at target tier" reporting.

## Running locally

```bash
pip install -e .

export DATABASE_URL="postgres://…"
export ANTHROPIC_API_KEY="sk-ant-…"

# Plan a Tier-1 sample
bidiq kb ingest --brand challenger --tier 1 --limit 5 --dry-run

# Tier-1 sample (5 PDFs, real)
bidiq kb ingest --brand challenger --tier 1 --limit 5

# Full Tier-1 across one brand (~$1–$3 for ~100 PDFs)
bidiq kb ingest --brand challenger --tier 1

# Manual Tier-2 upgrade for a specific row (this is what /api/knowledge-base/upgrade does)
bidiq kb ingest --tier 2 --id 3326

# Tier-2 across a brand (expensive — only for actively-bid brands)
bidiq kb ingest --brand challenger --tier 2

# Tier-2 with denser drawings — bigger chunks, more parallelism, higher DPI
bidiq kb ingest --tier 2 --id 3340 --chunk-size 8 --chunk-concurrency 6 --dpi 150

# Tier-2 with per-page descriptions saved alongside the body
bidiq kb ingest --tier 2 --id 3326 --include-page-summaries
```

Do not run Tier-1 across the full 3 058-PDF corpus casually — it's a paid, ~$30–$90 one-time job. Do it in controlled batches.

## On-demand upgrade flow

`POST /api/knowledge-base/upgrade` with body `{ "knowledge_item_id": 3326 }`:

1. Looks up the row.
2. If already Tier-2, returns `{ status: "already_tier_2", item }`.
3. Otherwise spawns `python3 -m bidiq.ingest --tier 2 --id <id>` and waits.
4. On success, returns the upgraded row.
5. Synchronous; caller waits ~30 s – 2 min depending on PDF size (chunked extraction means a 75-page manual targets <2 min wall-clock, not 10 min).

`/api/ask` calls this same Python helper (via `lib/upgrade.ts`) for any Tier-1 row in the top-5 retrieval slice, in parallel, then re-runs retrieval before answering.

### Runtime requirement

Both the `/api/knowledge-base/upgrade` endpoint and the auto-upgrade in `/api/ask` shell out to `python3 -m bidiq.ingest`. The runtime host therefore needs:

- Python 3.10+
- the `bidiq` package importable (`pip install -e .` in the repo)
- `poppler-utils` (for `pdf2image` / `pdfinfo`)

The Tier-1 batch ingestion is unaffected; it always runs from the CLI on a machine you control.

## Production behavior on Vercel

Vercel's Node serverless runtime does not include Python and cannot install OS packages, so the upgrade-via-subprocess flow won't run there. Rather than letting that 500 the request, the app **detects** the situation once at startup and **degrades gracefully**:

- On the first call to `lib/upgrade.ts`, we run `python3 -c "import bidiq"` once. If it succeeds, the upgrade flow is enabled. If it fails (Python missing, `bidiq` missing, or any spawn error), it's disabled. The result is cached in a module-level promise for the lifetime of the runtime — we never re-probe per request.
- **Local dev / self-host** (probe succeeds): unchanged behavior. `/api/ask` upgrades any Tier-1 row in the top-5 retrieval slice in parallel, re-runs retrieval, and answers with full Tier-2 content. `/api/knowledge-base/upgrade` runs the subprocess and returns the upgraded row.
- **Vercel production** (probe fails): `/api/ask` returns **200** with a thinner answer generated from `title + summary + tags + extracted_data` for the Tier-1 candidates. The shell-out is never attempted. The response includes `tier1_unupgraded_ids` so the client can see which sources were answered from metadata only, and `upgrade_available: false`. `/api/knowledge-base/upgrade` returns **503** with `{ status: "upgrade_unavailable", … }` — a clean signal callers can branch on, distinct from a real failure.

Each entry in `sources` carries a `tier: 1 | 2` field regardless of environment. The UI doesn't render this differently today, but the data is there to surface (e.g., a "shallow result — full content not yet extracted" badge) once we want to.

A future migration ("Option C") will move the upgrade work to a separate Python worker (queue + worker, or Lambda + container, or a long-running Fly/Railway box). Once that lands the Vercel app will dispatch to the worker instead of shelling out, and the degrade path here becomes obsolete. Until then, this behavior is the contract.

## Verifying output

```sql
-- Summary of tiers in the corpus
SELECT
  CASE
    WHEN raw_content IS NOT NULL AND length(raw_content) > 0 THEN 'tier-2'
    WHEN extracted_data->>'tier' = '1' THEN 'tier-1'
    ELSE 'unknown'
  END AS tier,
  count(*)
FROM knowledge_items
GROUP BY 1;

-- Recently ingested
SELECT id, title, category, brand_id, source_pages_count,
       extractor_version, extracted_at,
       extracted_data->>'tier' AS tier,
       raw_content IS NOT NULL AND length(raw_content) > 0 AS has_raw
FROM knowledge_items
WHERE extractor_version IN ('ingest.py-v1-tier1', 'ingest.py-v1-tier2', 'ingest.py-v1')
ORDER BY extracted_at DESC
LIMIT 10;
```

You can also hit the running Next.js app's `/api/ask` with a question targeting the brand. The first query against a Tier-1 row will spend 30 s – 2 min upgrading; subsequent queries return immediately. Source tiles in the response only show documents Claude actually cited in the answer (parsed from `[N]` markers).

### Backfilling search_text on existing rows

Earlier ingester versions truncated `search_text` to the first 5 000 characters of `raw_content`. New rows written by the current code use the full body, but rows already in the table need a one-time rebuild:

```bash
psql "$DATABASE_URL" -f scripts/migrations/2026-04-25-rebuild-search-text.sql
```

The script is idempotent and safe to re-run. Tier-1 rows (`raw_content` NULL) are unaffected — their `search_text` stays title + summary + tags. After running, spot-check a known-long row, e.g.:

```sql
SELECT id, length(search_text) AS st_len, length(raw_content) AS rc_len,
       search_text ILIKE '%anchor%' AS has_anchor
  FROM knowledge_items
 WHERE id = 3336;
-- Expect st_len ~84,000+, has_anchor = true.
```

## Errors

Per-PDF failures are tab-separated lines in `data/extraction-errors.log`:

```
<ISO timestamp>\t<relative path>\t<error class>\t<error first line>
```

One bad PDF never stops the run.

## What the ingester does NOT do

- Does not write to JSON files.
- Does not modify `data/product_data/` (source PDFs are read-only).
- Does not DELETE rows. Re-processing is an UPDATE.
- Does not update `brands` rows beyond inserting new ones. `we_carry` and `relationship_type` are Paul's to manage.
- Does not handle non-PDF files (`.xlsx`, `.md`, etc.). It logs a skip line and moves on.
- Does not touch `bidiq/enrich.py`.
- Does not migrate or relabel the legacy `ingest.py-v1` rows (3324–3326). Those are treated as Tier-2 by virtue of having `raw_content` populated.
