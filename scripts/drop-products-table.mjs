#!/usr/bin/env node
// One-shot cleanup: drop the legacy `products` table from Postgres.
// Run this ONLY after verifying that scripts/migrate-mohawk-jsons.mjs successfully
// moved all Mohawk data into knowledge_items.
//
// Usage: DATABASE_URL=postgres://... node scripts/drop-products-table.mjs

import { neon } from "@neondatabase/serverless";

if (!process.env.DATABASE_URL) {
  console.error("DATABASE_URL not set. Aborting.");
  process.exit(1);
}
const sql = neon(process.env.DATABASE_URL);

const exists = await sql`
  SELECT to_regclass('public.products') IS NOT NULL AS exists
`;
if (!exists[0].exists) {
  console.log("products table does not exist. Nothing to do.");
  process.exit(0);
}

const pre = await sql`SELECT count(*)::int AS c FROM products`;
console.log(`products row count before drop: ${pre[0].c}`);

await sql`DROP TABLE IF EXISTS products`;
console.log("products table dropped.");
