import { neon } from "@neondatabase/serverless";

let schemaReady = false;

export function getSQL() {
  const url = process.env.DATABASE_URL;
  if (!url) throw new Error("DATABASE_URL not configured");
  return neon(url);
}

export async function ensureSchema() {
  if (schemaReady) return;
  const sql = getSQL();

  await sql`
    CREATE TABLE IF NOT EXISTS brands (
      id SERIAL PRIMARY KEY,
      name TEXT NOT NULL UNIQUE,
      manufacturer_name TEXT,
      we_carry BOOLEAN DEFAULT FALSE,
      relationship_type TEXT DEFAULT 'unknown',
      notes TEXT,
      website TEXT,
      created_at TIMESTAMPTZ DEFAULT NOW(),
      updated_at TIMESTAMPTZ DEFAULT NOW(),
      CONSTRAINT brands_relationship_type_check CHECK (
        relationship_type IN ('own_vendor', 'competitor', 'reference', 'unknown')
      )
    )
  `;

  await sql`CREATE INDEX IF NOT EXISTS idx_brands_name ON brands(name)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_brands_we_carry ON brands(we_carry)`;

  await sql`
    CREATE TABLE IF NOT EXISTS knowledge_items (
      id SERIAL PRIMARY KEY,
      title TEXT NOT NULL,
      category TEXT[] NOT NULL DEFAULT '{}',
      subcategory TEXT,
      tags TEXT[] DEFAULT '{}',
      content_type TEXT NOT NULL DEFAULT 'text',
      source TEXT NOT NULL DEFAULT 'typed',
      source_filename TEXT,
      raw_content TEXT NOT NULL,
      extracted_data JSONB,
      summary TEXT,
      search_text TEXT NOT NULL DEFAULT '',
      created_at TIMESTAMPTZ DEFAULT NOW()
    )
  `;

  await sql`ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS brand_id INTEGER REFERENCES brands(id)`;
  await sql`ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS source_path TEXT`;
  await sql`ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS source_pages_count INTEGER`;
  await sql`ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS extracted_at TIMESTAMPTZ`;
  await sql`ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS extractor_version TEXT`;
  // external_url: a public, user-clickable URL for the underlying document.
  // For Rotary/Forward docs this is the manufacturer's S3 URL (Rotary hosts
  // these publicly already). For other brands it will be a Cloudflare R2
  // URL once we upload — until then it stays NULL and /api/documents/[id]/pdf
  // returns a clean 503.
  await sql`ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS external_url TEXT`;

  // Tier-1 (shallow) ingestion writes rows with raw_content NULL until a
  // Tier-2 upgrade fills in the full body.
  await sql`ALTER TABLE knowledge_items ALTER COLUMN raw_content DROP NOT NULL`;

  // Old btree index on the (now TEXT[]) category column; replaced by the GIN
  // index below. Dropping is idempotent on installs that never had it.
  await sql`DROP INDEX IF EXISTS idx_ki_category`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_category_gin ON knowledge_items USING GIN(category)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_search ON knowledge_items USING GIN(to_tsvector('english', coalesce(search_text, '')))`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_tags ON knowledge_items USING GIN(tags)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_brand_id ON knowledge_items(brand_id)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_source ON knowledge_items(source)`;

  // ---------- Iter15: vehicle-lift product catalog ----------
  //
  // Built on top of knowledge_items. The portal hits paginated GETs on these
  // tables to sync a structured product catalog without re-deriving SKUs from
  // PDFs every time. Pricing is INTENTIONALLY excluded — price sheets are used
  // as the source of truth for *which models exist*, not for their prices.
  // (Pricing comes later, possibly from different sheets / contracts.)

  await sql`
    CREATE TABLE IF NOT EXISTS products (
      id SERIAL PRIMARY KEY,
      brand_id INTEGER NOT NULL REFERENCES brands(id),
      sku TEXT NOT NULL,
      family_name TEXT,
      product_name TEXT,
      description TEXT,
      category TEXT NOT NULL DEFAULT 'unclassified',
      capacity_lbs INTEGER,
      is_ali_certified BOOLEAN DEFAULT FALSE,
      ali_cert_date DATE,
      variant_skus JSONB DEFAULT '[]'::jsonb,
      status TEXT NOT NULL DEFAULT 'unknown',
      source TEXT NOT NULL DEFAULT 'unknown',
      source_file TEXT,
      notes TEXT,
      created_at TIMESTAMPTZ DEFAULT NOW(),
      updated_at TIMESTAMPTZ DEFAULT NOW(),
      CONSTRAINT products_category_check CHECK (
        category IN (
          'two-post-lift', 'four-post-lift', 'scissor-lift', 'mobile-column',
          'light-duty-inground', 'heavy-duty-inground', 'vertical-rise-lift',
          'parallelogram-lift', 'low-rise-lift', 'rolling-jack', 'unclassified'
        )
      ),
      CONSTRAINT products_status_check CHECK (
        status IN ('current', 'discontinued', 'unknown')
      ),
      CONSTRAINT products_source_check CHECK (
        source IN ('price-sheet', 'kb-extraction', 'web-scrape', 'manual', 'unknown', 'svi-catalog')
      ),
      UNIQUE (brand_id, sku)
    )
  `;
  await sql`CREATE INDEX IF NOT EXISTS idx_products_brand_id ON products(brand_id)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_products_status ON products(status)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_products_capacity_lbs ON products(capacity_lbs)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku)`;
  // Search by variant SKU — the portal might query by an exact variant string
  // ("CL12A-LC-QC") and we need to find the family that lists it.
  await sql`CREATE INDEX IF NOT EXISTS idx_products_variant_skus_gin ON products USING GIN(variant_skus jsonb_path_ops)`;

  await sql`
    CREATE TABLE IF NOT EXISTS product_documents (
      id SERIAL PRIMARY KEY,
      product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
      knowledge_item_id INTEGER REFERENCES knowledge_items(id) ON DELETE SET NULL,
      doc_type TEXT NOT NULL DEFAULT 'other',
      is_primary BOOLEAN DEFAULT FALSE,
      pdf_url TEXT,
      notes TEXT,
      created_at TIMESTAMPTZ DEFAULT NOW(),
      CONSTRAINT product_documents_doc_type_check CHECK (
        doc_type IN (
          'spec-sheet', 'install-manual', 'service-manual',
          'parts-diagram', 'brochure', 'price-sheet', 'other'
        )
      ),
      UNIQUE (product_id, knowledge_item_id)
    )
  `;
  await sql`CREATE INDEX IF NOT EXISTS idx_pd_product_id ON product_documents(product_id)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_pd_knowledge_item_id ON product_documents(knowledge_item_id)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_pd_doc_type ON product_documents(doc_type)`;

  // product_external_refs: side table for catalog cross-references we
  // import from third-party sources (SVI International, the ALI directory,
  // future Sourcewell PCN listings, etc.). Lets us preserve every URL
  // a source publishes for a model without merging untrusted data into
  // the canonical products row — important when our internal catalog
  // is sourced from manufacturer price sheets and we trust those over
  // third-party listings.
  await sql`
    CREATE TABLE IF NOT EXISTS product_external_refs (
      id SERIAL PRIMARY KEY,
      product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
      source TEXT NOT NULL,            -- 'svi-catalog', 'ali-directory', ...
      external_sku TEXT,               -- the SKU as the source lists it
      external_make TEXT,              -- the make as the source lists it
      external_category TEXT,          -- the lift category as the source lists it
      page_url TEXT,                   -- canonical page for this model on the source's site
      resource_urls JSONB DEFAULT '[]'::jsonb,  -- array of all spec/manual/parts URLs
      notes TEXT,
      created_at TIMESTAMPTZ DEFAULT NOW(),
      updated_at TIMESTAMPTZ DEFAULT NOW(),
      UNIQUE (product_id, source, external_sku)
    )
  `;
  await sql`CREATE INDEX IF NOT EXISTS idx_per_product_id ON product_external_refs(product_id)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_per_source ON product_external_refs(source)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_per_external_sku ON product_external_refs(lower(external_sku))`;

  // Bump products.updated_at when the product's documents change.
  // Without this, doc-only edits (re-running match-kb-to-products,
  // adding a cross-link, flipping is_primary) wouldn't be visible to
  // a portal that polls /api/products and compares updated_at.
  await sql`
    CREATE OR REPLACE FUNCTION bump_product_updated_at_from_pd()
    RETURNS TRIGGER AS $$
    BEGIN
      IF (TG_OP = 'DELETE') THEN
        UPDATE products SET updated_at = NOW() WHERE id = OLD.product_id;
        RETURN OLD;
      ELSE
        UPDATE products SET updated_at = NOW() WHERE id = NEW.product_id;
        IF (TG_OP = 'UPDATE' AND OLD.product_id <> NEW.product_id) THEN
          UPDATE products SET updated_at = NOW() WHERE id = OLD.product_id;
        END IF;
        RETURN NEW;
      END IF;
    END;
    $$ LANGUAGE plpgsql
  `;
  await sql`DROP TRIGGER IF EXISTS trg_pd_bump_product ON product_documents`;
  await sql`
    CREATE TRIGGER trg_pd_bump_product
    AFTER INSERT OR UPDATE OR DELETE ON product_documents
    FOR EACH ROW EXECUTE FUNCTION bump_product_updated_at_from_pd()
  `;

  schemaReady = true;
}
