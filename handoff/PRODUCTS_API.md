# Products API — contract for the inventory portal

**Base URL**: `https://bid-iq-neon.vercel.app`

(Note: not `bid-iq.vercel.app` — that short alias is owned by an unrelated Vercel project. Our project lives at the `-neon` alias under the `pauljosephstern-1166s-projects` team. A custom domain is planned.)

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

- `current` — actively sold. Either in the manufacturer's most recent price sheet, OR listed as actively distributed in a trusted third-party catalog (e.g., SVI).
- `discontinued` — no longer in production / no longer distributed. ~828 rows are flagged this way (mostly SVI-sourced makes that SVI lists as discontinued at the make level).
- `unknown` — fallback.

### Source

Indicates how a row entered the catalog. The portal can filter on this to distinguish trusted-internal data from third-party imports.

- `price-sheet` — hand-curated from the manufacturer's current price sheet (the original 442 rows; this is our highest-trust data and our internal record)
- `svi-catalog` — imported from SVI International's distributor catalog (~1,814 rows). SVI is a third-party master distributor of vehicle lift parts and these listings reflect what SVI carries / sold historically. **Lower trust** than `price-sheet`: SVI lists many older models, models from defunct OEMs, and OEM-rebrands.
- `kb-extraction`, `web-scrape`, `manual`, `unknown` — reserved for future ingest paths.

### ALI certification

- `is_ali_certified = TRUE` — found in the ALI Lift Inspector Directory; `ali_cert_date` is populated
- `is_ali_certified = FALSE` — explicitly checked against ALI and not found (our original 442 went through this check)
- `is_ali_certified = NULL` — unknown. Default for everything imported from SVI: we haven't re-run the ALI match against SVI-sourced rows yet, and many SVI rows are old/defunct OEMs that have never been ALI-certified.

### External catalog references

A separate `product_external_refs` table records every third-party listing we know about for a product, including the listings we couldn't fold into a canonical row. Today it carries the 2,558 SVI catalog rows, each with the SVI page URL and up to 14 SVI resource endpoint URLs (spec sheets, parts manuals, etc.). The table is **not yet exposed via the API**; let me know if the portal wants it surfaced.

### Product images

Three product-image fields are returned on every row:

- `image_url` — URL to a product photo. Hosted on the manufacturer's CDN, an authorized reseller's CDN, or a vendor's image-hosting platform (Shopify, WordPress, etc.). The portal can `<img src="{image_url}">` directly. **Currently populated on 339 of 442 hand-curated products (76%);** SVI-imported rows have NULL `image_url`.
- `image_source_url` — the page the image was scraped from. Kept for audit; if a flagged image needs replacing, this is the page to verify against.
- `image_fetched_at` — when the scrape happened. After a manual re-scrape (`scripts/populate-product-images-v2.py --force`) this gets bumped.

Coverage by brand:

| brand | imaged | total | % |
|---|---:|---:|---:|
| challenger | 40 | 40 | 100% |
| forward | 22 | 22 | 100% |
| gray | 4 | 4 | 100% |
| mahle | 3 | 3 | 100% |
| bendpak | 82 | 84 | 97% |
| ari-hetra | 25 | 26 | 96% |
| rotary | 56 | 60 | 93% |
| coats | 24 | 27 | 88% |
| stertil-koni | 20 | 33 | 60% |
| mohawk | 24 | 39 | 62% |
| hunter | 7 | 12 | 58% |
| pks | 32 | 92 | 35% |
| **TOTAL** | **339** | **442** | **76%** |

The 103 uncovered rows are products without retail-facing pages anywhere on the public web — primarily PKS heavy-duty ingrounds and older Stertil-Koni/Mohawk/Hunter fleet variants sold via direct quote rather than catalog. They keep `image_url = NULL`.

**Quality:** the v2 scraper requires the SKU to appear in the source-page text (proves on-product relevance) and rejects placeholder/logo/favicon image filenames. After scrape, the catalog went through three rounds of human visual audit (Paul flagged bad matches via an HTML grid review; the rejections got cleared and re-scraped). Every URL in `image_url` has been seen by a human in iter-3 or earlier.

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
      "id": 65,
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
      "image_url": "https://challengerlifts.com/wp-content/uploads/2023/04/Screen-Shot-2023-04-14-at-9.42.34-AM.png",
      "image_source_url": "https://www.challengerlifts.com/CL12A",
      "image_fetched_at": "2026-05-17T...",
      "created_at": "2026-05-09T...",
      "updated_at": "2026-05-09T...",
      "documents": [...]   // present only if include_documents=true
    },
    ...
  ],
  "total": 2256,
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
      "product_id": 65,
      "knowledge_item_id": 3642,
      "doc_type": "install-manual",
      "is_primary": true,
      "pdf_url": "https://bid-iq-neon.vercel.app/api/documents/845/pdf",
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

### `GET /api/documents/[id]/pdf`

The clickable indirection for a product_document's underlying PDF. `id` is the `product_documents.id` (the same value returned as `documents[].id` from the `/api/products/...` endpoints, and the same value `pdf_url` is built from).

**Responses:**

| status | meaning |
|---|---|
| `302 Found` | The PDF is hosted publicly. The `Location` header points at the canonical URL (today: Rotary's public S3 for Rotary/Forward; later: a Cloudflare R2 URL for the rest). Open in a browser and it follows automatically. |
| `503 Service Unavailable` | The PDF exists in the bid-iq KB but hasn't been uploaded to object storage yet. Body is JSON with `document_id`, `product_id`, `doc_type`, `ki_id`, `ki_filename`, `ki_source_path`, `ki_title`, and an explanatory `message`. The portal can render this as "Coming soon" with the filename. |
| `404 Not Found` | No `product_documents` row with that id. |
| `400 Bad Request` | id is not a positive integer. |
| `500 Internal Server Error` | DB or upstream failure. Returns `{"error": "..."}`. |

**Why a redirect and not a direct URL on the product_documents row?** Stability. `pdf_url` is stored in the catalog and any client may cache it. If we migrate storage backends (today S3, tomorrow R2, day-after maybe a CDN in front of R2), the indirection means we never have to rewrite every stored URL — just update the redirect target.

## How to consume

1. **Initial sync**: `GET /api/products?page=1&page_size=200`, then page until `total_pages` is reached. Keep the full result indexed by `id` in the portal's local DB.
2. **Incremental sync**: poll `GET /api/products?page=...` daily; compare `updated_at` to detect changed rows. (Or use `created_at > last_sync` for new rows only — both fields are present.) See **Sync semantics** below for what `updated_at` is guaranteed to catch.
3. **Search**: pass `q` + filters straight from the portal's search UI.
4. **Display**: show `family_name` (or fall back to `product_name`, then `sku`). Show `capacity_lbs` and `category`. Variant SKUs are visible to the user as "configurations" — typically a dropdown or multi-line subtable.
5. **Documents**: deep-link the user to the doc via `pdf_url`. Every `product_documents` row now has a `pdf_url` of the form `https://bid-iq-neon.vercel.app/api/documents/<id>/pdf`. Hitting that URL:
   - **302 redirects** to the public PDF when one is available (today: all 399 Rotary + Forward docs are live via Rotary's public S3 hosting; tomorrow: BendPak / Mohawk / Challenger / Hunter / etc. ship after the R2 upload pass)
   - **503 returns JSON** with the filename and KB source path when the PDF isn't yet uploaded — so the portal can render "PDF coming soon" with useful context (filename, doc_type, ki_id) instead of a broken link
   
   The redirect is the indirection layer: `pdf_url` always stays the same in the catalog even when we migrate storage backends. The portal just opens `pdf_url` in a new tab and the browser follows the redirect.

   See `GET /api/documents/[id]/pdf` below for the full contract.

## Sync semantics — what `updated_at` catches

The catalog is hand-curated and grows over time as Paul enriches it (adding old/discontinued models, refining variant SKU lists, attaching newly-ingested manuals to product families). The portal can stay in sync by polling and comparing `updated_at`. Here's what's guaranteed to bump a row's `updated_at`:

| Change | Bumps `products.updated_at`? |
|---|---|
| New product family inserted | new `created_at` (and `updated_at`) |
| Family-level field edit (`family_name`, `description`, `category`, `capacity_lbs`, `is_ali_certified`, `variant_skus`, `notes`, …) | yes |
| `status` flip (`current` → `discontinued` and vice versa) | yes |
| Document linked to a product (new `product_documents` row) | yes — via DB trigger |
| Document removed from a product | yes — via DB trigger |
| `is_primary` flag flipped on a doc, or `doc_type` re-classified | yes — via DB trigger |
| Brand-level edit (rare) | no — but the join surfaces `brand_name` on every product |

Mechanism: `products.updated_at` is set to `NOW()` on every direct UPDATE, and an `AFTER INSERT/UPDATE/DELETE` trigger on `product_documents` propagates doc-level changes up to the parent product. So a single timestamp watch is enough — the portal does **not** need to separately poll `/api/products/[id]/documents` to detect doc changes.

### Deletes vs. discontinues

**Preferred:** mark old or removed models with `status='discontinued'`. They stay visible via the API; the portal sees the status change through `updated_at` and can treat them as historical. This is how Paul tracks anything that's no longer on a manufacturer's current price sheet but is still a real product.

**If a row is hard-deleted** (rare — typically only when a row was created in error): the row vanishes from `/api/products` results entirely, and there is no event for it. The portal should diff its full known-id set against the latest sync result every N days and prune any local rows whose ids are no longer returned. Pseudocode:

```
known_ids = {row.id for row in local_db.products}
live_ids  = set()
for page in 1..total_pages:
    live_ids |= {p.id for p in GET /api/products?page=…}
removed = known_ids - live_ids
local_db.delete_or_archive(removed)
```

### Doc-level deltas without re-fetching everything

When `products.updated_at` indicates a change but the portal already has the family-level fields it cares about, fetch just `GET /api/products/[id]/documents` to refresh the doc list. That endpoint returns the same doc shape as the inlined `documents` array on `GET /api/products/[id]`.

## Current data state (v2)

- **2,256 product rows total**, split by source:
  - **442 hand-curated families** (`source='price-sheet'`) across our 12 carry-brands (BendPak, Challenger, Mohawk, Rotary, Hunter, Stertil-Koni, ARI-Hetra, PKS, Coats, Mahle, Forward, Gray). This is our internal truth. ALI certification status is populated on every row (TRUE or FALSE). The portal team should treat these as the high-confidence layer.
  - **1,814 SVI-imported rows** (`source='svi-catalog'`) across 73 makes (most of which are NEW brands we don't otherwise carry — defunct OEMs, regional brands, OEM-rebrands). 828 of these are flagged `status='discontinued'` per SVI's make-level disposition. ALI cert is `NULL` (Unknown) for all of them.
- **1,715+ variant SKUs** under the 442 hand-curated families
- **2,558 rows in `product_external_refs`** holding SVI page URLs + resource endpoint URLs, joined to the products they describe (both pre-existing and newly-imported)
- **1,048 product_documents** linked, **189 / 442 (42%) of hand-curated families** have ≥1 doc
- Per-brand coverage:

  | brand | families | w/ docs | cov% | links |
  |---|---:|---:|---:|---:|
  | bendpak | 84 | 53 | 63% | 277 |
  | challenger | 40 | 29 | 72% | 70 |
  | rotary | 60 | 34 | 56% | 297 |
  | mohawk | 39 | 25 | 64% | 248 |
  | forward | 22 | 18 | 81% | 102 |
  | hunter | 12 | 7 | 58% | 29 |
  | mahle | 3 | 3 | 100% | 3 |
  | gray | 4 | 4 | 100% | 4 |
  | pks | 92 | 12 | 13% | 14 |
  | coats | 27 | 2 | 7% | 2 |
  | ari-hetra | 26 | 1 | 3% | 1 |
  | stertil-koni | 33 | 1 | 3% | 1 |

  PKS / Coats / ARI-Hetra / Stertil-Koni have thin coverage because most of their manuals aren't in the KB yet.

## Caveats

- **No pricing in this API.** Price sheets fed the model list but pricing is intentionally excluded — it'll come later via separate contract endpoints.
- **`pdf_url` is now populated on every `product_documents` row** — it points at `https://bid-iq-neon.vercel.app/api/documents/<id>/pdf`, which 302-redirects to the underlying PDF. **991 of 1,048 docs (94%)** resolve to a live PDF today (Rotary + Forward, served from Rotary's public S3). The remaining 649 return a 503 JSON with the filename + `ki_source_path` until those brands' PDFs are uploaded to R2. The portal's "view PDF" button should open `pdf_url` in a new tab — the browser follows the redirect for working docs and shows the 503 JSON for pending ones.
- **No write endpoints.** This is a one-way sync; the portal owns its own state.
- **`status` defaults to no filter** — pass `status=current` if you only want what's in the latest manufacturer price sheets.
- **Some brands have thin doc coverage** (Stertil-Koni, Forward, Mahle, Gray). That'll improve as we ingest those manufacturers' PDFs into the KB.

## Smoke-test cURL

```bash
# Full first page
curl 'https://bid-iq-neon.vercel.app/api/products?page=1&page_size=20'

# All BendPak two-post lifts in a capacity window
curl 'https://bid-iq-neon.vercel.app/api/products?brand=bendpak&category=two-post-lift&capacity_min=10000&capacity_max=20000'

# Look up a specific variant SKU
curl 'https://bid-iq-neon.vercel.app/api/products?sku=CL12A-LC-QC'

# Full detail for one product
curl 'https://bid-iq-neon.vercel.app/api/products/65'

# Just the documents
curl 'https://bid-iq-neon.vercel.app/api/products/65/documents'

# Free-text search with documents inlined
curl 'https://bid-iq-neon.vercel.app/api/products?q=mobile%20column&include_documents=true'
```

## Open questions for the portal team

1. **Pagination cadence** — is 50/page fine, or do you want larger pages for fewer round-trips on initial sync?
2. **Authentication** — these endpoints are unauthenticated today. Do you need API-key or token auth before going prod?
3. **PDF download** — R2 / S3 / streaming-from-Vercel? Confirm preference and we'll wire `pdf_url`.
4. **Webhook on update?** — alternative to polling. Probably not needed at v1 but worth flagging.
