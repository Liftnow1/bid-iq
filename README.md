# bid-iq

Bid IQ is Liftnow's internal tool for ingesting government bid packages and assembling complete responses — pricing, strategy, spec analysis, compliance forms, and submittal documentation. The goal is one-click bid completion, with user input only where judgement is required.

## Architecture

- **Postgres (Neon serverless)** is the single source of truth. Two tables matter:
  - `brands` — every manufacturer/brand we track (Liftnow, Mohawk, BendPak, Rotary, Challenger, Stertil-Koni, Hunter, etc.), with `we_carry` and `relationship_type` flags.
  - `knowledge_items` — every queryable piece of knowledge: product specs, pricing, bid history, installation guides, compliance data, competitive intel, customer intel. Each row is classified with **one or more** tags from the 56-category v4-trimmed vocabulary; `category` is `TEXT[]`.
- **Next.js 16 app** (`app/`) serves the UI and API routes. Deployed via Vercel.
- **Anthropic Claude** handles classification (`/api/knowledge-base/ingest`) and Q&A (`/api/ask`).

### Classification vocabulary

The authoritative classifier system prompt — including the full 56-tag list, decision cues, multi-tag heuristics, and 20 worked examples — lives at [`docs/classifier-system-prompt-v1.md`](docs/classifier-system-prompt-v1.md). The ingester loads it at module import time; updating that file is how you change classifier behavior.

A document typically receives 1–4 tags. Examples:

- A Challenger 4018 IOM PDF covering install + operation + service + parts → `[installation-guides, operation-manuals, service-procedures, parts-catalog]`.
- A Sourcewell-published price sheet → `[procurement-process, list-pricing]`.
- A filled NJ PWCR form → `[certified-payroll]`.

Agent-tier filtering constants (Paul-only, email-agent excludes, content-engine allow-list) are defined in [`lib/category-tiers.ts`](lib/category-tiers.ts) for future use; `/api/ask` doesn't enforce them today.

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

## Deployment notes

- `.github/workflows/extract-lfs-pdfs.yml.disabled` — the old Mohawk-only PDF extraction workflow, disabled by filename suffix. Do not re-enable; it will be replaced by the new ingester.
- Vercel picks up `app/` and `vercel.json` as usual. `DATABASE_URL` and `ANTHROPIC_API_KEY` must be set in the Vercel project.

## Brand voice

The product is **Liftnow** — lowercase `n`, one word. Never "LiftNow", never "LIFTNOW".
