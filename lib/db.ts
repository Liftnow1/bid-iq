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
        source IN ('price-sheet', 'kb-extraction', 'web-scrape', 'manual', 'unknown')
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

  schemaReady = true;
}
