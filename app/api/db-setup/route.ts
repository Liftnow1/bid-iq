import { NextResponse } from "next/server";
import { getSQL } from "@/lib/db";

export async function POST() {
  try {
    const sql = getSQL();

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
        search_text TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ DEFAULT NOW()
      )
    `;

    await sql`CREATE INDEX IF NOT EXISTS idx_products_manufacturer ON products(manufacturer)`;
    await sql`CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)`;
    await sql`CREATE INDEX IF NOT EXISTS idx_products_model ON products(model)`;
    await sql`CREATE INDEX IF NOT EXISTS idx_products_search ON products USING GIN(to_tsvector('english', search_text))`;

    const result = await sql`SELECT count(*) as count FROM products`;

    return NextResponse.json({
      status: "ok",
      message: "Schema created",
      rows: result[0].count,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
