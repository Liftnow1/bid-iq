#!/usr/bin/env node
// Bootstrap Postgres schema for bid-iq.
// Mirrors the DDL defined in lib/db.ts ensureSchema().
//
// Usage: DATABASE_URL=postgres://... node scripts/setup-db.mjs

import { neon } from "@neondatabase/serverless";

if (!process.env.DATABASE_URL) {
  console.error("DATABASE_URL not set. Aborting.");
  process.exit(1);
}

const sql = neon(process.env.DATABASE_URL);

async function setup() {
  console.log("Creating schema...");

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

  // Tier-1 ingestion leaves raw_content NULL until upgrade.
  await sql`ALTER TABLE knowledge_items ALTER COLUMN raw_content DROP NOT NULL`;

  await sql`CREATE INDEX IF NOT EXISTS idx_ki_category ON knowledge_items(category)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_search ON knowledge_items USING GIN(to_tsvector('english', coalesce(search_text, '')))`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_tags ON knowledge_items USING GIN(tags)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_brand_id ON knowledge_items(brand_id)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_ki_source ON knowledge_items(source)`;

  const b = await sql`SELECT count(*)::int AS c FROM brands`;
  const k = await sql`SELECT count(*)::int AS c FROM knowledge_items`;
  console.log(`Schema ensured. brands=${b[0].c}  knowledge_items=${k[0].c}`);
}

setup().catch((e) => {
  console.error(e);
  process.exit(1);
});
