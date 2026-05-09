# bid-iq

Bid IQ is Liftnow's internal tool for ingesting government bid packages and assembling complete responses — pricing, strategy, spec analysis, compliance forms, and submittal documentation. The goal is one-click bid completion, with user input only where judgement is required.

## Architecture

- **Postgres (Neon serverless)** is the single source of truth. Four tables matter:
  - `brands` — every manufacturer/brand we track (Liftnow, Mohawk, BendPak, Rotary, Challenger, Stertil-Koni, Hunter, etc.), with `we_carry` and `relationship_type` flags.
  - `knowledge_items` — every queryable piece of knowledge: product specs, pricing, bid history, installation guides, compliance data, competitive intel, customer intel. Each row is classified into one of three access tiers (`tier-1-public`, `tier-2-internal`, `tier-3-paul-only`, plus `uncategorized` for documents that can't be classified confidently). `category` is `TEXT[]` — the schema supports multi-tagging for future use, but the v2 classifier always returns a single-element array.
  - `products` — structured vehicle-lift catalog. One row per **family** (e.g. Hunter `RX16K`), with all configurable variants collapsed into a `variant_skus` JSONB array. Family-level fields: `brand_id`, `sku`, `family_name`, `category` (10 lift categories + `unclassified`), `capacity_lbs`, `is_ali_certified`, `status`. Hand-curated from manufacturer price sheets via `scripts/parse-price-sheet.py`, round-tripped through Excel for human review (`export-products-to-excel.py` / `import-products-from-excel.py`).
  - `product_documents` — M:N join from `products` to `knowledge_items`. Each row links a product family to a doc with a `doc_type` (`spec-sheet`, `install-manual`, `service-manual`, `parts-diagram`, `brochure`, `price-sheet`, `other`). `is_primary = true` flags the canonical doc per `(product, doc_type)` pair (longest body wins).
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
| `GET  /api/products` | Paginated product catalog with brand / category / capacity / status / sku / free-text filters |
| `GET  /api/products/[id]` | Full product detail with all linked documents |
| `GET  /api/products/[id]/documents` | Just the documents for one product |

The product catalog endpoints are documented in detail at [`docs/PRODUCTS_API.md`](docs/PRODUCTS_API.md) — that's the contract for downstream consumers (e.g. the inventory portal). It's read-only and unauthenticated today.

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

## Product catalog pipeline

The hand-curated lift catalog lives in `products` + `product_documents`. Its build pipeline:

```bash
# 1. Parse a manufacturer price sheet (xlsx or PDF) into product rows
python scripts/parse-price-sheet.py <path> [--brand <name>] [--guidance-file <md>] [--wipe-brand]

# 2. Export the live catalog to xlsx for human review (round-trip)
python scripts/export-products-to-excel.py --out data/pillar2-staging/products-master-vN.xlsx

# 3. Re-import an edited xlsx (diffs hidden id column against DB; applies inserts/updates/deletes)
python scripts/import-products-from-excel.py <edited.xlsx>

# 4. Match knowledge_items (PDFs already in the KB) to product rows
python scripts/match-kb-to-products.py [--brand <name>] [--with-fallback]

# Brand-specific linkers (idempotent)
python scripts/link-forward-from-rotary-csv.py        # parses data/product_data/rotary/rotary_lookup.csv
python scripts/cross-link-forward-el-variants.py      # shares CR14/CRA14/CRO14/1000MCL docs with EL/DT trims
```

Current state: **442 product families across 12 brands**, **1,048 product↔doc links**, **189 / 442 (42%)** families have ≥1 doc. Stertil-Koni / ARI-Hetra / Coats / PKS are sparsely covered because their manuals aren't in the KB yet.

## Deployment notes

- `.github/workflows/extract-lfs-pdfs.yml.disabled` — the old Mohawk-only PDF extraction workflow, disabled by filename suffix. Do not re-enable; it will be replaced by the new ingester.
- Vercel picks up `app/` and `vercel.json` as usual. `DATABASE_URL` and `ANTHROPIC_API_KEY` must be set in the Vercel project.

## Brand voice

The product is **Liftnow** — lowercase `n`, one word. Never "LiftNow", never "LIFTNOW".
