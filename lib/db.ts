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

  schemaReady = true;
}
