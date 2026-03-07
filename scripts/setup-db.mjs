import { neon } from '@neondatabase/serverless';

const DATABASE_URL = process.env.DATABASE_URL || 'postgresql://neondb_owner:npg_CVPZOwiW6gU0@ep-gentle-shadow-adw4fmoz-pooler.c-2.us-east-1.aws.neon.tech/neondb?sslmode=require';

const sql = neon(DATABASE_URL);

async function setup() {
  console.log('Creating schema...');

  await sql`CREATE EXTENSION IF NOT EXISTS vector`;

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
      search_text TEXT,
      created_at TIMESTAMPTZ DEFAULT NOW()
    )
  `;

  await sql`CREATE INDEX IF NOT EXISTS idx_products_manufacturer ON products(manufacturer)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_products_model ON products(model)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_products_search ON products USING GIN(to_tsvector('english', coalesce(search_text, '')))`;

  const result = await sql`SELECT count(*) FROM products`;
  console.log('Schema created. Current rows:', result[0].count);
}

setup().catch(e => { console.error(e); process.exit(1); });
