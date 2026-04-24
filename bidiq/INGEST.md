# bidiq PDF ingestion

Two Python tools live in `bidiq/`. They are NOT interchangeable.

| Tool | Scope | Output | Status |
| --- | --- | --- | --- |
| `bidiq/enrich.py` | Mohawk-only installation drawings and spec PDFs | JSON files in `kb_output/` | Historical / legacy. Do not extend. |
| `bidiq/ingest.py` | Any brand, any document type under `data/product_data/<brand>/` | Rows in the Postgres `knowledge_items` table | Active. Use this for new work. |

## When to run what

- **New PDFs arrive under `data/product_data/<brand>/`** → `ingest.py`.
- **You want a row that the Next.js app (`/api/ask`, `/api/knowledge-base/*`) can read** → `ingest.py`.
- **You are re-reading old Mohawk JSONs** → those were already migrated into Postgres; no action needed.
- **You are looking at `enrich.py`** → leave it alone. It is retained as a historical artifact.

## What `ingest.py` does

1. Walks `data/product_data/` recursively (or just `data/product_data/<brand>/` if `--brand` is passed).
2. Skips hidden files and non-PDFs (with a log line).
3. For every distinct brand folder, looks up the brand row in `brands` by case-insensitive name. Inserts a new row only if no match exists, with `we_carry = FALSE`, `relationship_type = 'unknown'`, `notes = 'Auto-created by ingest.py'`. Never updates or deletes existing brand rows.
4. For each PDF:
   - Checks for an existing `knowledge_items` row with matching `source_path`. If `extracted_at` is newer than the PDF's mtime, skips.
   - Rasterizes pages to JPEGs (respecting Claude's 5 MB / 8000 px limits), the same pattern as `enrich.py`.
   - Calls Claude vision with a generalized classify + extract prompt (10-category vocabulary).
   - UPSERTs a row in `knowledge_items` with: `title`, `category`, `tags`, `content_type='pdf'`, `source='ingested'`, `source_filename`, `source_path`, `source_pages_count`, `summary`, `raw_content` (= the extracted markdown body), `extracted_data` (JSONB containing `effective_date`, `supersedes_previous`, `pages_summary`), `search_text`, `brand_id`, `extracted_at = NOW()`, `extractor_version = 'ingest.py-v1'`.
   - On failure, appends a line to `data/extraction-errors.log` and continues.

## Schema notes (for future Claude sessions)

The build brief used a column name `content`; the actual schema (see `lib/db.ts`) uses `raw_content`. The ingester writes to `raw_content`. Structured side-data (effective date, supersedes flag, per-page descriptions) goes into the existing `extracted_data` JSONB column rather than new columns. `search_text` is a NOT NULL column and is built the same way `app/api/knowledge-base/ingest/route.ts` builds it: `title + summary + tags + first 5000 chars of content`.

## CLI

```
bidiq kb ingest [OPTIONS]
```

Or directly: `python -m bidiq.ingest [OPTIONS]`.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--brand <name>` | (none) | Restrict the walker to `data/product_data/<name>/` only. Folder names are lowercase (`challenger`, `mohawk`, `bendpak`, etc.). |
| `--limit <n>` | (none) | Only process the first N PDFs that are NOT already up-to-date. Use for testing. |
| `--dry-run` | off | List what would be processed and skipped. No API calls, no DB writes. Does not require `ANTHROPIC_API_KEY`, and only uses `DATABASE_URL` if it's set (to report already-processed PDFs accurately). |
| `--concurrency <n>` | 3 | Number of parallel workers (each holds its own DB connection + Anthropic client). |
| `--dpi <n>` | 150 | DPI passed to `pdf2image`. Matches `enrich.py`. |
| `--model <id>` | `claude-sonnet-4-20250514` | Anthropic model for vision extraction. |

## Required env vars

- `DATABASE_URL` — Postgres connection string (the same one the Next.js app uses).
- `ANTHROPIC_API_KEY` — Anthropic API key.

Both are required for real runs. `--dry-run` only needs `DATABASE_URL` if you want accurate "already processed" reporting.

## Running it locally

```bash
# Install Python deps once
pip install -e .

# Export env
export DATABASE_URL="postgres://…"
export ANTHROPIC_API_KEY="sk-ant-…"

# Dry run (verify plan)
bidiq kb ingest --brand challenger --limit 3 --dry-run

# Real test run (3 PDFs, 1 worker for easier debugging)
bidiq kb ingest --brand challenger --limit 3 --concurrency 1

# Full brand (after sanity-checking on the small sample)
bidiq kb ingest --brand challenger
```

Do not run across all 2,982 PDFs casually — that's a paid, long-running job. Do it in controlled batches.

## Verifying output

After a real run:

```sql
SELECT id, title, category, brand_id, source_path, source_pages_count,
       extracted_at, extractor_version, jsonb_pretty(extracted_data)
FROM knowledge_items
WHERE extractor_version = 'ingest.py-v1'
ORDER BY extracted_at DESC
LIMIT 10;
```

You can also hit the running Next.js app's `/api/ask` endpoint with a question like `"what Challenger lifts do we have"` — ingested rows should show up in the `sources` array.

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
- Does not deep-parse service manuals page-by-page — the full text goes into `raw_content`, category is `service-procedures` (or `installation-guides`), and that's it. Page-level parts extraction is Phase 2.
- Does not handle non-PDF files (`.xlsx`, `.md`, etc.). It logs a skip line and moves on.
- Does not touch `bidiq/enrich.py`.
