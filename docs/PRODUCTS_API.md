# Products API — contract for the inventory portal

**Base URL**: `https://bid-iq.vercel.app` (or your preview alias)

This API exposes the vehicle-lift product catalog the portal will sync. It's read-only HTTP. The catalog is hand-curated from manufacturer price sheets (current models only, ~12 brands) and tied to a knowledge base of installation manuals, spec sheets, service manuals, brochures, and parts diagrams.

## Schema overview

```
products
  id, brand_id, brand_name, sku, family_name, product_name, description,
  category, capacity_lbs, is_ali_certified, ali_cert_date,
  variant_skus[], status, source, source_file, notes, created_at, updated_at

product_documents
  id, product_id, knowledge_item_id, doc_type, is_primary, pdf_url,
  notes, created_at
  + flattened: ki_title, ki_filename, ki_source_path, ki_body_chars
```

### Categories (10 + unclassified)

`two-post-lift`, `four-post-lift`, `scissor-lift`, `mobile-column`, `light-duty-inground`, `heavy-duty-inground`, `vertical-rise-lift`, `parallelogram-lift`, `low-rise-lift`, `rolling-jack`, `unclassified`

### Doc types (7)

`spec-sheet`, `install-manual`, `service-manual`, `parts-diagram`, `brochure`, `price-sheet`, `other`

`is_primary = true` marks the canonical doc per `(product, doc_type)` pair (longest body wins on tiebreak).

### Variant SKUs

A `product` row is one **family**. The `variant_skus` JSONB array carries every configured SKU under that family — different finishes, mount types, runway lengths, etc. all collapse into one product row. So `RX16K` (Hunter) is one product with 15 variant SKUs like `RX16KLF`, `RX16KLFBLK`, `RX16KLFIS`, etc.

If you query by SKU and the user types a variant string (e.g. `RX16KLFIS`), the API returns the family. Use `GET /api/products?sku=<exact>` — the matcher checks both `products.sku` and the `variant_skus` array.

### Status

- `current` — model is in the manufacturer's most recent price sheet (default filter)
- `discontinued` — KB-only model (older PDF in our archive but not in current price sheet) — **none populated yet, ignore for now**
- `unknown` — fallback

## Endpoints

### `GET /api/products`

Paginated list with filtering.

**Query params**

| param | type | example | notes |
|---|---|---|---|
| `brand` | string | `bendpak` | single-brand filter (case-insensitive) |
| `brands` | csv | `bendpak,rotary` | multi-brand filter |
| `category` | string | `two-post-lift` | single category |
| `categories` | csv | `two-post-lift,four-post-lift` | multi-category |
| `capacity_min` | int | `10000` | inclusive lower bound, lbs |
| `capacity_max` | int | `20000` | inclusive upper bound, lbs |
| `status` | string | `current` | `current` / `discontinued` / `unknown`. **Default: no filter (returns all)** |
| `sku` | string | `CL12A` | exact match against family sku OR variant_skus |
| `q` | string | `mobile column` | ILIKE on family_name + product_name + description + sku |
| `page` | int | `1` | 1-indexed; default 1 |
| `page_size` | int | `50` | default 50, max 200 |
| `include_documents` | bool | `true` | nest documents per product (default false) |

**Response**

```json
{
  "products": [
    {
      "id": 12,
      "brand_id": 3,
      "brand_name": "challenger",
      "sku": "CL12A",
      "family_name": "Challenger CL12A",
      "product_name": "Challenger CL12A 2-Post Surface-Mounted Lift",
      "description": "12,000 lb capacity, 2-post symmetric design, 175.5\" overall height...",
      "category": "two-post-lift",
      "capacity_lbs": 12000,
      "is_ali_certified": true,
      "ali_cert_date": "2023-01-27",
      "variant_skus": ["CL12A-LC", "CL12A-QC", "CL12A-LC-QC", "CL12A-DPC-QC", "CL12A-1", "CL12A-2", "CL12A-1-QC", "CL12A-2-QC"],
      "status": "current",
      "source": "price-sheet",
      "source_file": "Challenger Lifts - Current SW 04-20-2026.xlsx",
      "notes": null,
      "created_at": "2026-05-09T...",
      "updated_at": "2026-05-09T...",
      "documents": [...]   // present only if include_documents=true
    },
    ...
  ],
  "total": 417,
  "page": 1,
  "page_size": 50,
  "total_pages": 9
}
```

### `GET /api/products/[id]`

Full product detail with all documents.

**Response**

```json
{
  "product": { ...same fields as above... },
  "documents": [
    {
      "id": 845,
      "product_id": 12,
      "knowledge_item_id": 3642,
      "doc_type": "install-manual",
      "is_primary": true,
      "pdf_url": null,
      "notes": null,
      "created_at": "2026-05-09T...",
      "ki_title": "Challenger CL12A Product Manual",
      "ki_filename": "CL12A Product Manual - CL12A-IOM-A-2025-04-08.pdf",
      "ki_source_path": "data/product_data/challenger/CL12A Product Manual - CL12A-IOM-A-2025-04-08.pdf",
      "ki_body_chars": 55834
    },
    ...
  ]
}
```

### `GET /api/products/[id]/documents`

Just the documents for a product. Useful for refreshing doc lists without re-fetching the whole product. Same document shape as above.

## How to consume

1. **Initial sync**: `GET /api/products?page=1&page_size=200`, then page until `total_pages` is reached. Keep the full result indexed by `id` in the portal's local DB.
2. **Incremental sync**: poll `GET /api/products?page=...` daily; compare `updated_at` to detect changed rows. (Or use `created_at > last_sync` for new rows only — both fields are present.)
3. **Search**: pass `q` + filters straight from the portal's search UI.
4. **Display**: show `family_name` (or fall back to `product_name`, then `sku`). Show `capacity_lbs` and `category`. Variant SKUs are visible to the user as "configurations" — typically a dropdown or multi-line subtable.
5. **Documents**: deep-link the user to the doc. **PDF URLs are not yet populated** (`pdf_url` is null on every row in v1). For now, the document's `ki_filename` + `ki_source_path` are the canonical references. **A future iteration will upload PDFs to object storage (R2 likely) and populate `pdf_url`.** Until then, the portal can either:
   - Display the filename as text and rely on out-of-band file delivery, or
   - Wait for the storage rollout (~1 week)

## Current data state (v1)

- **417 product families** across 12 brands (BendPak, Challenger, Mohawk, Rotary, Hunter, Stertil-Koni, ARI-Hetra, PKS, Coats, Mahle, Forward, Gray) — Omer parked pending a price list
- **1,715 variant SKUs** across those families
- **967 product_documents** linked to products (most coverage on Rotary, BendPak, Mohawk, Challenger, Hunter; sparse on Stertil/Forward/Mahle/Gray — those brands' product PDFs aren't yet in the KB)

## Caveats

- **No pricing in this API.** Price sheets fed the model list but pricing is intentionally excluded — it'll come later via separate contract endpoints.
- **`pdf_url` always null in v1.** PDF storage decision still pending.
- **No write endpoints.** This is a one-way sync; the portal owns its own state.
- **`status` defaults to no filter** — pass `status=current` if you only want what's in the latest manufacturer price sheets.
- **Some brands have thin doc coverage** (Stertil-Koni, Forward, Mahle, Gray). That'll improve as we ingest those manufacturers' PDFs into the KB.

## Smoke-test cURL

```bash
# Full first page
curl 'https://bid-iq.vercel.app/api/products?page=1&page_size=20'

# All BendPak two-post lifts in a capacity window
curl 'https://bid-iq.vercel.app/api/products?brand=bendpak&category=two-post-lift&capacity_min=10000&capacity_max=20000'

# Look up a specific variant SKU
curl 'https://bid-iq.vercel.app/api/products?sku=CL12A-LC-QC'

# Full detail for one product
curl 'https://bid-iq.vercel.app/api/products/12'

# Just the documents
curl 'https://bid-iq.vercel.app/api/products/12/documents'

# Free-text search with documents inlined
curl 'https://bid-iq.vercel.app/api/products?q=mobile%20column&include_documents=true'
```

## Open questions for the portal team

1. **Pagination cadence** — is 50/page fine, or do you want larger pages for fewer round-trips on initial sync?
2. **Authentication** — these endpoints are unauthenticated today. Do you need API-key or token auth before going prod?
3. **PDF download** — R2 / S3 / streaming-from-Vercel? Confirm preference and we'll wire `pdf_url`.
4. **Webhook on update?** — alternative to polling. Probably not needed at v1 but worth flagging.
