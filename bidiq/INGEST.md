# bidiq/ingest.py — general-purpose PDF ingester

`ingest.py` walks `data/product_data/<brand>/` recursively, classifies and
extracts each PDF with Claude vision, and writes the result into the
`knowledge_items` Postgres table.

## When to use this vs `enrich.py`

| Use `ingest.py`                              | Use `enrich.py`                                       |
| -------------------------------------------- | ----------------------------------------------------- |
| Any new ingestion work, any brand            | Never (kept as a historical Mohawk-only artifact)     |
| Writes directly to Postgres `knowledge_items`| Writes JSON files to `kb_output/`                     |
| 10-category classification + free-form tags  | Mohawk drawing-specific schema (model/dims/parts BoM) |

`enrich.py` stays in the repo unchanged; do not extend it.

## Prerequisites

- `python >= 3.10`
- Poppler installed (for `pdf2image`): `apt install poppler-utils` on Linux,
  `brew install poppler` on macOS.
- The Postgres schema from `lib/db.ts` / `scripts/setup-db.mjs` must already
  be in place (`brands` and `knowledge_items` tables).
- Environment variables:
  - `DATABASE_URL` — Postgres connection string (same one the Next.js app uses)
  - `ANTHROPIC_API_KEY` — Claude API key

Install Python deps:

```bash
pip install -e .            # picks up pyproject.toml
# or, ad hoc:
pip install anthropic click pdf2image Pillow 'psycopg[binary]>=3.1'
```

## CLI

Two equivalent entry points:

```bash
# As a subcommand on the main CLI:
bidiq kb ingest [OPTIONS]

# Or directly:
python -m bidiq.ingest [OPTIONS]
```

| Flag             | Default                        | Meaning                                                                         |
| ---------------- | ------------------------------ | ------------------------------------------------------------------------------- |
| `--brand <n>`    | (all brand folders)            | Restrict to PDFs under `data/product_data/<n>/`. Folder name, case-sensitive.   |
| `--limit <n>`    | (no limit)                     | Process only the first N **unprocessed** PDFs. Useful for smoke tests.          |
| `--dry-run`      | off                            | Print the plan; make no API calls and no DB writes (does not even open the DB). |
| `--concurrency`  | `3`                            | Worker threads. Each worker uses its own Postgres connection.                   |
| `--dpi`          | `150`                          | Pdf2image rasterization DPI (matches `enrich.py`).                              |
| `--model`        | `claude-sonnet-4-20250514`     | Claude model used for vision extraction.                                        |

## Example commands

```bash
# Plan a small Challenger run, no side effects
bidiq kb ingest --brand challenger --limit 3 --dry-run

# Real run: extract 3 Challenger PDFs and write to Postgres
bidiq kb ingest --brand challenger --limit 3

# Full run for a single brand
bidiq kb ingest --brand mohawk --concurrency 4

# Full run across every brand (long-running; expect hours for ~3,000 PDFs)
bidiq kb ingest --concurrency 4
```

## What the ingester does

For each PDF under `data/product_data/<brand>/...`:

1. **Brand registration.** The brand name is the path segment immediately
   under `data/product_data/`. If that brand is not in the `brands` table, it
   is inserted with `we_carry = FALSE`, `relationship_type = 'unknown'`,
   `notes = 'Auto-created by ingest.py'`. Existing brand rows are never
   updated — Paul flips `we_carry` and `relationship_type` manually.

2. **Idempotency check.** The PDF's repo-root-relative path (e.g.
   `data/product_data/challenger/CL10A.pdf`) is the natural key in
   `knowledge_items.source_path`. If a row already exists and its
   `extracted_at` is newer than the file's mtime, the PDF is skipped.

3. **Rasterization.** Pages are rendered to JPEG at the requested DPI and
   encoded as base64, downscaled when needed to stay under Claude's 5 MB and
   8000 px-per-dimension limits. Pages are sent to Claude in batches of 20.

4. **Vision extraction.** Claude returns JSON with: `category` (one of the 10
   locked categories), `title`, `summary`, `effective_date`,
   `supersedes_previous`, `tags`, `content_markdown`, `pages_summary`.

5. **DB write.** A row in `knowledge_items` is INSERTed (or UPDATEd if
   `source_path` already exists) with:

   | Column                | Source                                       |
   | --------------------- | -------------------------------------------- |
   | `title`               | extraction `title`                           |
   | `category`            | extraction `category` (forced into the 10)   |
   | `subcategory`         | NULL (reserved)                              |
   | `tags`                | extraction `tags`, brand always appended     |
   | `content_type`        | `'pdf'`                                      |
   | `source`              | `'ingested'`                                 |
   | `source_filename`     | basename                                     |
   | `source_path`         | repo-relative POSIX path                     |
   | `source_pages_count`  | page count from rasterization                |
   | `summary`             | extraction `summary`                         |
   | `raw_content`         | extraction `content_markdown`                |
   | `extracted_data`      | full JSON extraction (effective_date, supersedes_previous, pages_summary, …) |
   | `search_text`         | `title + summary + tags + raw_content[:5000]` |
   | `brand_id`            | FK to `brands` row                           |
   | `extracted_at`        | `NOW()`                                      |
   | `extractor_version`   | `'ingest.py-v1'`                             |

6. **Error isolation.** A failure on one PDF appends a tab-separated line to
   `data/extraction-errors.log` and the run continues.

## Schema-mapping notes (re: brief vs reality)

The build brief lists a `content` column on `knowledge_items`. The actual
post-migration schema (from `lib/db.ts`) calls that column `raw_content`
(NOT NULL) and adds two more required-or-useful columns the brief does not
mention:

- `search_text TEXT NOT NULL DEFAULT ''` — populated for the FTS GIN index
- `extracted_data JSONB` — used by other code paths to keep the structured
  extraction beyond what `raw_content` carries

The ingester maps the brief's "content" to `raw_content`, populates
`search_text` to keep `/api/ask`'s full-text search working, and stashes the
full extraction JSON (including `effective_date`, `supersedes_previous`, and
`pages_summary`, none of which have dedicated columns) in `extracted_data`.
This matches what `app/api/knowledge-base/ingest/route.ts` already does.

## Verification

After a real run, a quick sanity check from `psql`:

```sql
SELECT id, brand_id, category, title, source_pages_count,
       extractor_version, extracted_at
  FROM knowledge_items
 WHERE extractor_version = 'ingest.py-v1'
 ORDER BY extracted_at DESC
 LIMIT 10;

SELECT b.name, count(*)
  FROM knowledge_items ki
  JOIN brands b ON b.id = ki.brand_id
 WHERE ki.extractor_version = 'ingest.py-v1'
 GROUP BY b.name
 ORDER BY 2 DESC;
```

Or hit the running app:

```bash
curl -X POST http://localhost:3000/api/ask \
  -H 'content-type: application/json' \
  -d '{"question":"What Challenger lifts do we have?"}'
```

The response's `sources` array should include rows with
`extractor_version: 'ingest.py-v1'` and `brand_name: 'challenger'`.

## What it does NOT do

- Does not write to JSON files (output is Postgres-only).
- Does not modify source PDFs under `data/product_data/`.
- Does not delete `knowledge_items` rows. Re-processing UPDATEs in place.
- Does not update existing `brands` rows. Only inserts newly-discovered brands.
- Does not deep-parse manuals page-by-page like `enrich.py` does for Mohawk
  drawings. Manuals are captured holistically; per-page parts-list mining is
  a Phase 2 concern.
- Does not handle non-PDF files. `.xlsx`, `.md`, etc. are silently skipped.
- Does not touch `bidiq/enrich.py`.
