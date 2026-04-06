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
    CREATE TABLE IF NOT EXISTS products (
      id SERIAL PRIMARY KEY,
      manufacturer TEXT NOT NULL DEFAULT 'mohawk',
      category TEXT,
      model TEXT,
      variant TEXT,
      product_type TEXT,
      capacity TEXT,
      document_type TEXT,
      source_file TEXT,
      data JSONB NOT NULL,
      search_text TEXT NOT NULL DEFAULT '',
      created_at TIMESTAMPTZ DEFAULT NOW()
    )
  `;

  await sql`CREATE INDEX IF NOT EXISTS idx_products_manufacturer ON products(manufacturer)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_products_model ON products(model)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_products_search ON products USING GIN(to_tsvector('english', coalesce(search_text, '')))`;

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

  await sql`CREATE INDEX IF NOT EXISTS idx_ki_category ON knowledge_items(category)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_search ON knowledge_items USING GIN(to_tsvector('english', coalesce(search_text, '')))`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_tags ON knowledge_items USING GIN(tags)`;

  schemaReady = true;
}
