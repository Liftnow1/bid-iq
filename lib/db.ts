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
      category TEXT NOT NULL,
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

  await sql`CREATE INDEX IF NOT EXISTS idx_ki_category ON knowledge_items(category)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_search ON knowledge_items USING GIN(to_tsvector('english', coalesce(search_text, '')))`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_tags ON knowledge_items USING GIN(tags)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_brand_id ON knowledge_items(brand_id)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_source ON knowledge_items(source)`;

  schemaReady = true;
}
