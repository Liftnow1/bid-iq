# bid-iq → portal handoff

Everything Paul's colleague (and their Claude) needs to start consuming the Liftnow vehicle-lift catalog from the bid-iq backend. **Read `PRODUCTS_API.md` first.** Then look at the sample responses to see real shapes.

## What's in this folder

| File | Purpose |
|---|---|
| `PRODUCTS_API.md` | Full API contract — endpoints, query params, response shapes, sync semantics, deletes-vs-discontinues, cURL smoke tests. **Read this first.** |
| `current-catalog-snapshot.xlsx` | The live catalog as of today (442 product families, 12 brands). One row per family, with hidden id column, frozen header, autofilter, dropdowns on `category`/`status`/`is_ali_certified`. README sheet inside explains columns. |
| `sample-responses/products-page1.json` | Real `GET /api/products?page=1&page_size=3` response. |
| `sample-responses/product-detail.json` | Real `GET /api/products/[id]` for the most-documented product. |
| `sample-responses/product-documents.json` | Real `GET /api/products/[id]/documents` for the same product. |
| `sample-responses/products-include-documents.json` | `GET /api/products?include_documents=true` (page 1) — same shape as above with `documents` inlined per product. |
| `sample-responses/generate-samples.py` | Regenerator. Hits the live DB and rebuilds all four sample JSONs. Run after schema or data changes. |

## TL;DR for the portal

- **Base URL:** `https://bid-iq.vercel.app` (once iter15 is merged to main)
- **Read-only, unauthenticated** in v1. We can add API-key auth before going prod — flag it.
- **Initial sync:** page through `GET /api/products?page=N&page_size=200` until `total_pages` is reached. Index locally by `id`.
- **Incremental sync:** poll daily, compare `updated_at` per row to detect changes. Family-level edits, status flips, AND document-level changes (linked / unlinked / re-classified) all bump `products.updated_at` via DB trigger. One timestamp watch is enough.
- **Discontinued models:** Paul marks them `status='discontinued'` rather than deleting. They stay in the API; portal sees the change through `updated_at`. Hard-deletes are rare — handle them by diffing the full known-id set against each sync.
- **PDFs:** `pdf_url` is `null` in v1. Storage is the next decision (R2 / S3 / Vercel-streaming). Until then, use `ki_filename` + `ki_source_path` as canonical references.
- **No pricing.** Price sheets fed the catalog but pricing is intentionally not exposed here — separate contract endpoints later.

## What the portal can do today

- Browse 442 product families across 12 lift brands (BendPak, Challenger, Mohawk, Rotary, Hunter, Forward, Stertil-Koni, ARI-Hetra, PKS, Coats, Mahle, Gray)
- Filter by brand, category (10 lift categories), capacity range, status, exact SKU (matches family OR variant), free-text
- Show 1,715+ variant SKUs as configurations under each family
- Surface 1,048 linked manuals / spec sheets / parts diagrams / service manuals via `product_documents`
- 81% of Forward families covered, 100% Mahle / Gray, 60-70% BendPak / Challenger / Mohawk / Rotary / Hunter

## What the portal should NOT assume

- That `pdf_url` will be populated soon (decision pending)
- That every brand has full doc coverage — Stertil-Koni / ARI-Hetra / Coats / PKS are sparsely covered because their manuals aren't in the KB yet (Paul is working on it)
- That endpoints are authenticated (they aren't, yet)
- That the schema is frozen — we may add fields. New fields will be additive; existing fields' shapes are stable.

## Open questions for the portal team

These are flagged in `PRODUCTS_API.md` too:

1. **Pagination cadence** — is `page_size=50` fine, or do you want larger pages for fewer round-trips on initial sync? (max is 200)
2. **Authentication** — API-key or token before prod?
3. **PDF download** — preferred storage layout (R2 / S3 / streaming-from-Vercel)? Once decided, `pdf_url` gets populated.
4. **Webhook on update?** — alternative to polling. Probably not needed at v1 but worth flagging.

Paul will wire in answers as they come in.

## When this folder gets out of date

Re-running `sample-responses/generate-samples.py` (with `DATABASE_URL` set in the env or `.env`/`.env.local`) refreshes the four sample JSONs against the current DB. Re-running `python scripts/export-products-to-excel.py --out handoff/current-catalog-snapshot.xlsx` from the bid-iq repo root refreshes the xlsx snapshot. `PRODUCTS_API.md` is a copy of `docs/PRODUCTS_API.md` — keep them in sync (edit the canonical one in `docs/`, then `cp`).
