# bid-iq

Bid IQ is Liftnow's internal tool for ingesting government bid packages and assembling complete responses — pricing, strategy, spec analysis, compliance forms, and submittal documentation. The goal is one-click bid completion, with user input only where judgement is required.

## Architecture

- **Postgres (Neon serverless)** is the single source of truth. Two tables matter:
  - `brands` — every manufacturer/brand we track (Liftnow, Mohawk, BendPak, Rotary, Challenger, Stertil-Koni, Hunter, etc.), with `we_carry` and `relationship_type` flags.
  - `knowledge_items` — every queryable piece of knowledge: product specs, pricing, bid history, installation guides, compliance data, competitive intel, customer intel. Each row is classified into one of three access tiers (`tier-1-public`, `tier-2-internal`, `tier-3-paul-only`, plus `uncategorized` for documents that can't be classified confidently). `category` is `TEXT[]` — the schema supports multi-tagging for future use, but the v2 classifier always returns a single-element array.
- **Next.js 16 app** (`app/`) serves the UI and API routes. Deployed via Vercel.
- **Anthropic Claude** handles classification (`/api/knowledge-base/ingest`) and Q&A (`/api/ask`).

### Classification vocabulary — 3-tier access model

The authoritative classifier system prompt lives at [`docs/classifier-system-prompt-v2.md`](docs/classifier-system-prompt-v2.md) and is loaded by the ingester at module import time; updating that file is how you change classifier behavior. The retired 56-category v4-trimmed prompt is preserved at [`docs/classifier-system-prompt-v1-DEPRECATED.md`](docs/classifier-system-prompt-v1-DEPRECATED.md) for historical reference only.

The three tiers map directly to the agent architecture:

| Tier | Who can see it | Examples |
| --- | --- | --- |
| `tier-1-public` | Content Engine, Email Agent, future Bid Tracker, Paul | manufacturer product docs, industry references, government regulations, Sourcewell published pricing, Liftnow capability statements / case studies / sales collateral, Paul's voice samples |
| `tier-2-internal` | Email Agent, future Bid Tracker, Paul (no Content Engine) | customer quotes / POs / contracts, service & install records, vendor & subcontract agreements, Liftnow credentials (W-9, COIs), internal SOPs, compliance templates, RFPs received and responses |
| `tier-3-paul-only` | Paul only (no agent ever) | vendor cost pricing, customer invoices, payment / banking records, insurance policies & bonds, certified payroll, bid protests, change orders, competitive intelligence, win/loss debriefs, financials, commission reports, employment docs, M&A / legal correspondence |

When a document straddles tiers the most-restrictive tier wins. See `docs/classifier-system-prompt-v2.md` for the full decision rule and worked examples.

Agent-tier filtering constants are defined in [`lib/category-tiers.ts`](lib/category-tiers.ts) for future use; `/api/ask` doesn't enforce them today.

## Key routes

| Route | Purpose |
| --- | --- |
| `/` | Chat UI against the knowledge base |
| `/knowledge-base` | Add, browse, classify, delete knowledge items |
| `POST /api/ask` | Q&A: `{question}` in, `{answer, sources[]}` out |
| `GET  /api/ask` | Health/counts by category |
| `POST /api/knowledge-base/ingest` | Classify + insert text or uploaded file |
| `GET/DELETE /api/knowledge-base/items` | List, search, delete items |
| `POST /api/db-setup` | Ensure schema (idempotent) |

## Running locally

Required env vars (e.g. in `.env.local`):

```
DATABASE_URL=postgres://...            # Neon connection string
ANTHROPIC_API_KEY=sk-ant-...
```

```bash
npm install
npm run dev            # http://localhost:3000
```

Ensure the schema:

```bash
DATABASE_URL=... node scripts/setup-db.mjs
```

## Data layout

- `data/product_data/<brand>/<category>/*.pdf` — canonical PDF storage (Hunter, BendPak, Rotary, Challenger, Stertil-Koni, Mohawk, etc.). Tracked via Git LFS.
- `data/catalog.db` — **historical artifact.** A SQLite catalog (3,058 products, 111 brands) imported from ALI. Already migrated into Postgres via `scripts/migrate-catalog-db.mjs`. Not queried by the running app. Will be deleted in a later cleanup pass.
- `data/ali_lifts.json` — raw ALI directory scrape; not yet wired up.
- `kb_extracted/`, `kb_output/` — legacy Mohawk-only extractions. Migrated into `knowledge_items` via `scripts/migrate-mohawk-jsons.mjs`. Retained until the new ingester lands.

The ingestion pipeline that reads PDFs under `data/product_data/` and writes to `knowledge_items` is a **separate, forthcoming component.** It does not exist yet. `bidiq/enrich.py` is the retained historical Mohawk-specific extractor and will not be part of the new pipeline.

## One-shot migration scripts

Run these once, in order, with `DATABASE_URL` set:

```bash
# 1. Create / update schema
node scripts/setup-db.mjs

# 2. Import SQLite catalog into Postgres
node --experimental-sqlite scripts/migrate-catalog-db.mjs

# 3. Import legacy Mohawk JSON extractions
node scripts/migrate-mohawk-jsons.mjs

# 4. (After verifying #2 and #3 landed cleanly) retire the old products table
node scripts/drop-products-table.mjs
```

All scripts are idempotent — re-running them inserts nothing new and skips existing rows.

## Sort Report Mode

For pre-ingest review of the Phase 2 staging corpus, run:

```bash
python scripts/sort_report/run_sort_report.py [--dry-run] [--limit N]
```

This walks the priority + secondary buckets under
`bidiq-phase2-staging/`, applies a local exclusion list, classifies each
remaining file with the v2.1 prompt, and writes `SORT-REPORT.csv` at the
staging root. **No database writes** — Paul reviews the CSV in Excel
before deciding what to actually ingest.

The exclusion list lives in the `BIDIQ_EXCLUSION_STRINGS` env var
(pipe-delimited, e.g. `BIDIQ_EXCLUSION_STRINGS=string-a|string-b`). Set
it in `.env.local` (gitignored — never committed). Files whose name or
first-pages text contains any exclusion string are moved to
`99-EXCLUDED-PERSONAL/` before classification, so they never reach the
Claude API.

A second list, `BIDIQ_SKIP_PATTERNS` (pipe-delimited shell globs), short-
circuits known-noise filenames (e.g. OCR-split page-fragments of a
single project) to `tier=uncategorized` with no extraction and no API
call. Matched files are *moved* into `98-SKIPPED-PATTERN/` so they
leave the active priority/secondary buckets and don't get re-processed
on subsequent runs. Recoverable — delete that folder when satisfied.
Combinable with the repeatable `--skip-pattern GLOB` CLI flag. Match
is case-insensitive against the bare filename.

The classifier system prompt loaded by `bidiq.ingest` is
`docs/classifier-system-prompt-v2.1.md`. The sort-report runner
additionally injects a few-shot block of Paul's hand-corrected verdicts
on a 30-file spot-check, with prompt caching so the extra tokens are
charged at the cached rate.

## Deployment notes

- `.github/workflows/extract-lfs-pdfs.yml.disabled` — the old Mohawk-only PDF extraction workflow, disabled by filename suffix. Do not re-enable; it will be replaced by the new ingester.
- Vercel picks up `app/` and `vercel.json` as usual. `DATABASE_URL` and `ANTHROPIC_API_KEY` must be set in the Vercel project.

## Brand voice

The product is **Liftnow** — lowercase `n`, one word. Never "LiftNow", never "LIFTNOW".
