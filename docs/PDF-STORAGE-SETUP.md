# PDF storage setup — Cloudflare R2

Bid-iq's `/api/documents/[id]/pdf` route 302-redirects to the public URL stored in `knowledge_items.external_url`. Today that field is populated for the 1,444 Rotary/Forward docs hosted on Rotary's public S3. The remaining ~640 docs (BendPak, Mohawk, Challenger, Hunter, PKS, etc.) live only on Paul's local disk and need to be uploaded to a public-readable bucket.

We chose **Cloudflare R2** because:
- **10 GB / month storage is free** (forever) — our total payload is ~1.6 GB
- **$0 egress** (Cloudflare doesn't charge for downloads, ever)
- S3-compatible API → standard boto3 client works as-is
- Adding a custom domain is straightforward once Cloudflare manages your DNS

The total monthly cost at our scale is **$0**. If we grow to 100 GB, it becomes $1.35/mo. Vercel Blob and AWS S3 are both ~10× more expensive once you factor in egress.

---

## One-time setup (Paul does this)

### 1. Cloudflare account + R2 enabled

1. Sign in or sign up at <https://dash.cloudflare.com>.
2. Left sidebar → **R2 Object Storage**.
3. Click **Purchase R2** (misleading button — there's no charge for the free tier; Cloudflare wants a card on file for if you exceed it). Accept terms.

### 2. Create the bucket

1. **R2 → Create bucket**.
2. Name: `bid-iq-docs` (or whatever you want — note it down).
3. Default location: **Automatic** (Cloudflare picks nearest data center).
4. Default storage class: **Standard**.
5. Click **Create bucket**.

### 3. Make it public

Two options — pick one:

**Option A (fast, free, ugly URL):** R2.dev managed subdomain.
1. Open the bucket → **Settings** tab → **Public access** section.
2. Under **R2.dev subdomain**, click **Allow Access**, confirm.
3. Cloudflare assigns a public URL like `https://pub-<32-char-hash>.r2.dev`. Copy it.

**Option B (clean URL, requires DNS):** Custom domain.
1. Make sure the domain (e.g. `bid-iq.com` or `liftnow.com`) is on Cloudflare DNS.
2. Bucket → **Settings** → **Public access** → **Custom Domains** → **Connect Domain**.
3. Enter the subdomain you want, e.g. `files.bid-iq.com`. Cloudflare auto-creates the CNAME record.
4. Wait ~30 seconds for it to provision.
5. Use `https://files.bid-iq.com` as the public base URL.

Option A is fine for now; you can swap to a custom domain later by setting `external_url` via an UPDATE.

### 4. Generate an API token

1. Top-right → **R2 → Manage R2 API Tokens** (or directly: <https://dash.cloudflare.com/?to=/:account/r2/api-tokens>).
2. **Create API token**.
3. **Token name**: `bid-iq uploader`.
4. **Permissions**: **Object Read & Write**.
5. **Specify bucket(s)**: pick **Apply to specific buckets only** → select `bid-iq-docs`.
6. **TTL**: leave as **Forever** (or set an expiry if you prefer).
7. **Create API Token**.
8. Cloudflare shows three values **once**. Copy them all immediately into a password manager:
   - **Access Key ID**
   - **Secret Access Key**
   - **Endpoint** (an URL of the form `https://<account-id>.r2.cloudflarestorage.com`)
9. Also note your **Account ID** — visible in the R2 dashboard's right-side panel.

### 5. Add to `.env` and `.env.local`

```env
CLOUDFLARE_R2_ACCOUNT_ID=<account-id>           # the UUID from R2 dashboard
CLOUDFLARE_R2_ACCESS_KEY_ID=<access-key>
CLOUDFLARE_R2_SECRET_ACCESS_KEY=<secret>
CLOUDFLARE_R2_BUCKET=bid-iq-docs
CLOUDFLARE_R2_PUBLIC_BASE_URL=https://pub-<hash>.r2.dev   # or https://files.bid-iq.com
```

No trailing slash on the public base URL.

### 6. Also add the same vars to Vercel project env

This is the only step the bid-iq Vercel deployment cares about — once `external_url` is populated in the DB, the `/api/documents/[id]/pdf` route just redirects to those URLs. Vercel doesn't need to talk to R2 directly at request time. But adding the vars to Vercel is good hygiene for any future server-side R2 access.

1. <https://vercel.com/pauljosephstern-1166s-projects/bid-iq/settings/environment-variables>
2. Add each of the 5 vars above for **Production**, **Preview**, **Development**.
3. Redeploy is **not** required (the route doesn't read these vars).

---

## Running the upload (Claude does this once credentials are set)

```bash
# Smoke test — uploads 5 files
python scripts/upload-pdfs-to-r2.py --limit 5 --verbose

# Verify in DB
psql $DATABASE_URL -c "SELECT id, source_filename, external_url FROM knowledge_items WHERE external_url LIKE 'https://pub-%' LIMIT 5"

# Full upload (~10 min for 1.56 GB on a typical home connection)
python scripts/upload-pdfs-to-r2.py --verbose

# Brand-by-brand if you prefer
python scripts/upload-pdfs-to-r2.py --brand bendpak
python scripts/upload-pdfs-to-r2.py --brand mohawk
```

The script is **idempotent**. It checks `external_url IS NULL` in the DB before uploading, and HEADs the R2 object before re-uploading. Safe to re-run after partial failures.

Object key layout: `kb/<knowledge_item_id>/<sanitized-filename>.pdf` — the `ki_id` prefix prevents filename collisions across brands.

---

## After the upload

The `/api/documents/[id]/pdf` route already does the right thing — it 302-redirects to `external_url`. So the moment the script writes URLs to the DB, every previously-503 doc becomes a working 302. No code redeploy needed.

Sanity check from outside:

```bash
curl -sI 'https://bid-iq-neon.vercel.app/api/documents/349/pdf'
# Should now return: HTTP/2 302 / Location: https://pub-xxx.r2.dev/kb/4238/...
```

The portal team's catalog sync (the daily `GET /api/products` poll) will see `products.updated_at` bumped on every product whose docs got URLs (via the `product_documents → products` trigger from iter15.5), and the new `pdf_url` field will be present in their response.

---

## Cost monitoring

- R2 dashboard → bucket → **Metrics** tab shows storage size, request counts, egress (egress should always be $0).
- Set up a **Notification** at 8 GB if you're worried about brushing the 10 GB free ceiling.
- If you ever DO exceed the free tier, the bill comes in at $0.015/GB/month for storage. 100 GB = $1.50/mo. There is no egress charge.

---

## Migrating to a different storage backend later

If you ever want to move off R2 (to S3, GCS, B2, etc.), the migration is:

1. Copy all objects from R2 → new backend.
2. Update `external_url` in the DB to point at the new URLs:
   ```sql
   UPDATE knowledge_items
   SET external_url = replace(external_url, 'https://pub-xxx.r2.dev', 'https://new-host')
   WHERE external_url LIKE 'https://pub-xxx.r2.dev/%';
   ```
3. `products.updated_at` gets bumped via trigger automatically; portal picks up new URLs on next sync.

Because the public-facing URL is `/api/documents/[id]/pdf` (not the R2 URL directly), end users never see the backend swap. That's the whole point of the indirection layer from iter19.
