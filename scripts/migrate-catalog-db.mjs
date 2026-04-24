#!/usr/bin/env node
// One-shot migration: data/catalog.db (SQLite) -> Postgres
//   brands + manufacturers -> brands
//   catalog_products       -> knowledge_items (category='product-specifications')
//   catalog_accessories    -> logged to stdout, not migrated (only 4 rows)
//   spec_sheet_records     -> skipped (empty)
//
// Idempotent: skips inserts when a row with the same natural key already exists.
// Natural keys:
//   brands: name
//   knowledge_items: (source='catalog-db-migration', title)
//
// Usage:  DATABASE_URL=postgres://... node --experimental-sqlite scripts/migrate-catalog-db.mjs

import { DatabaseSync } from "node:sqlite";
import { neon } from "@neondatabase/serverless";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CATALOG_DB = path.resolve(__dirname, "..", "data", "catalog.db");

if (!process.env.DATABASE_URL) {
  console.error("DATABASE_URL not set. Aborting.");
  process.exit(1);
}

const sql = neon(process.env.DATABASE_URL);
const sqlite = new DatabaseSync(CATALOG_DB, { readOnly: true });

function toBool(v) {
  return v === 1 || v === true || v === "1" || v === "true";
}

function normalizeRelationshipType(rt) {
  const allowed = new Set(["own_vendor", "competitor", "reference", "unknown"]);
  if (!rt || typeof rt !== "string") return "unknown";
  const trimmed = rt.trim();
  if (!trimmed) return "unknown";
  return allowed.has(trimmed) ? trimmed : "unknown";
}

function formatProductContent(p, brandName, manufacturerName) {
  const lines = [];
  const push = (k, v) => {
    if (v === null || v === undefined) return;
    if (typeof v === "string" && v.trim() === "") return;
    lines.push(`${k}: ${v}`);
  };
  push("Brand", brandName);
  push("Manufacturer", manufacturerName);
  push("Model", p.model);
  push("Model (normalized)", p.model_normalized);
  push("Category", p.category);
  push("Lift Type", p.lift_type);
  push("Capacity (lbs)", p.capacity_lbs);
  push("Max Rise (in)", p.max_rise_in);
  push("Overall Height (in)", p.overall_height_in);
  push("Overall Width (in)", p.overall_width_in);
  push("Drive-Through Clearance (in)", p.drive_through_clearance_in);
  push("Power Requirements", p.power_requirements);
  push("ALI Certified", toBool(p.ali_certified) ? "yes" : null);
  push("ALI Cert Number", p.ali_cert_number);
  push("ALI Certification Date", p.ali_certification_date);
  push("ALI Notes", p.ali_notes);
  push("ETL Listed", toBool(p.etl_listed) ? "yes" : null);
  push("ANSI Compliant", toBool(p.ansi_compliant) ? "yes" : null);
  push("ALI Manufacturer Address", p.ali_manufacturer_address);
  push("ALI Manufacturer Phone", p.ali_manufacturer_phone);
  push("ALI Manufacturer Email", p.ali_manufacturer_email);
  push("Dealer Cost", p.dealer_cost);
  push("MSRP", p.msrp);
  push("Weight (lbs)", p.weight_lbs);
  push("Ships From", p.ships_from);
  push("Freight Class", p.freight_class);
  push("Spec Sheet URL", p.spec_sheet_url);
  push("Product Page URL", p.product_page_url);
  push("Data Source", p.data_source);
  push("Notes", p.notes);
  return lines.join("\n");
}

function buildSummary(p, brandName) {
  const parts = [];
  if (p.lift_type) parts.push(p.lift_type);
  else if (p.category) parts.push(p.category);
  if (p.capacity_lbs) parts.push(`${Number(p.capacity_lbs).toLocaleString()} lb capacity`);
  if (toBool(p.ali_certified)) {
    const d = p.ali_certification_date && String(p.ali_certification_date).trim();
    parts.push(d ? `ALI-certified ${d}` : "ALI-certified");
  }
  const prefix = brandName ? `${brandName} ${p.model || ""}`.trim() : p.model || "";
  const tail = parts.join(", ");
  if (prefix && tail) return `${prefix}: ${tail}.`;
  if (prefix) return `${prefix}.`;
  if (tail) return `${tail}.`;
  return "Catalog product.";
}

function buildTags(p, brandName, manufacturerName) {
  const tags = new Set();
  if (brandName) tags.add(brandName.toLowerCase());
  if (manufacturerName && manufacturerName.toLowerCase() !== (brandName || "").toLowerCase()) {
    tags.add(manufacturerName.toLowerCase());
  }
  if (p.lift_type) tags.add(String(p.lift_type).toLowerCase().replace(/\s+/g, "-"));
  else if (p.category) tags.add(String(p.category).toLowerCase().replace(/\s+/g, "-"));
  if (toBool(p.ali_certified)) tags.add("ali-certified");
  if (toBool(p.etl_listed)) tags.add("etl-listed");
  if (toBool(p.ansi_compliant)) tags.add("ansi-compliant");
  return Array.from(tags);
}

function buildSearchText(p, brandName, manufacturerName, title, summary, content, tags) {
  return [
    title,
    brandName,
    manufacturerName,
    p.model,
    p.model_normalized,
    p.lift_type,
    p.category,
    summary,
    ...tags,
    content,
  ]
    .filter((v) => v !== null && v !== undefined && String(v).trim() !== "")
    .join(" ");
}

async function migrateBrands() {
  const manufacturersById = new Map();
  for (const m of sqlite.prepare("SELECT * FROM manufacturers").all()) {
    manufacturersById.set(m.id, m);
  }

  const existing = await sql`SELECT name, id FROM brands`;
  const existingByName = new Map(existing.map((r) => [r.name, r.id]));

  let inserted = 0;
  let skipped = 0;
  let errors = 0;

  for (const b of sqlite.prepare("SELECT * FROM brands").all()) {
    const mfg = manufacturersById.get(b.manufacturer_id);
    const manufacturerName = mfg ? mfg.name : null;
    const website = mfg ? mfg.website || null : null;
    const notes = b.notes && String(b.notes).trim() ? b.notes : null;
    const relationship = normalizeRelationshipType(b.relationship_type);
    const weCarry = toBool(b.we_carry);

    if (existingByName.has(b.name)) {
      skipped++;
      continue;
    }
    try {
      const rows = await sql`
        INSERT INTO brands (name, manufacturer_name, we_carry, relationship_type, notes, website)
        VALUES (${b.name}, ${manufacturerName}, ${weCarry}, ${relationship}, ${notes}, ${website})
        ON CONFLICT (name) DO NOTHING
        RETURNING id
      `;
      if (rows.length > 0) {
        existingByName.set(b.name, rows[0].id);
        inserted++;
      } else {
        skipped++;
      }
    } catch (e) {
      errors++;
      console.error(`  brand '${b.name}' error: ${e.message}`);
    }
  }
  return { inserted, skipped, errors, brandIdByName: existingByName, manufacturersById };
}

async function migrateProducts(brandIdByName, manufacturersById) {
  const sqliteBrandsById = new Map();
  for (const b of sqlite.prepare("SELECT * FROM brands").all()) {
    sqliteBrandsById.set(b.id, b);
  }

  let inserted = 0;
  let skipped = 0;
  let errors = 0;

  const products = sqlite.prepare("SELECT * FROM catalog_products").all();
  const titleCounts = new Map();

  for (const p of products) {
    const brand = sqliteBrandsById.get(p.brand_id);
    const brandName = brand ? brand.name : null;
    const mfg = manufacturersById.get(p.manufacturer_id);
    const manufacturerName = mfg ? mfg.name : null;

    let title = brandName && p.model ? `${brandName} ${p.model}` : p.model || `Catalog Product #${p.id}`;
    const n = (titleCounts.get(title) || 0) + 1;
    titleCounts.set(title, n);
    if (n > 1) title = `${title} (#${p.id})`;

    const content = formatProductContent(p, brandName, manufacturerName);
    const summary = buildSummary(p, brandName);
    const tags = buildTags(p, brandName, manufacturerName);
    const searchText = buildSearchText(p, brandName, manufacturerName, title, summary, content, tags);
    const brandId = brandName ? brandIdByName.get(brandName) || null : null;

    const existing = await sql`
      SELECT id FROM knowledge_items
      WHERE source = 'catalog-db-migration' AND title = ${title}
      LIMIT 1
    `;
    if (existing.length > 0) {
      skipped++;
      continue;
    }

    try {
      await sql`
        INSERT INTO knowledge_items (
          title, category, subcategory, tags, content_type, source,
          source_filename, raw_content, extracted_data, summary, search_text,
          brand_id, extractor_version
        ) VALUES (
          ${title},
          ${"product-specifications"},
          ${p.lift_type || p.category || null},
          ${tags},
          ${"catalog"},
          ${"catalog-db-migration"},
          ${null},
          ${content || title},
          ${JSON.stringify({ catalog_product_id: p.id, raw: p })},
          ${summary},
          ${searchText},
          ${brandId},
          ${"catalog-db-migration"}
        )
      `;
      inserted++;
    } catch (e) {
      errors++;
      console.error(`  product id=${p.id} model=${p.model} error: ${e.message}`);
    }
  }
  return { inserted, skipped, errors, total: products.length };
}

function logAccessories() {
  const rows = sqlite.prepare(
    `SELECT a.id, a.name, a.part_number, a.description, a.dealer_cost, a.msrp,
            p.model as product_model, b.name as brand_name
       FROM catalog_accessories a
       LEFT JOIN catalog_products p ON p.id = a.product_id
       LEFT JOIN brands b ON b.id = p.brand_id`
  ).all();
  console.log(`\nAccessories (${rows.length} rows — NOT migrated, preserved here for record):`);
  for (const r of rows) {
    console.log(
      `  #${r.id}  ${r.brand_name || "?"} / ${r.product_model || "?"}  "${r.name}"  ` +
        `part='${r.part_number || ""}'  msrp=${r.msrp ?? "null"}`
    );
  }
}

async function main() {
  console.log(`Reading SQLite: ${CATALOG_DB}`);
  console.log("Target Postgres via DATABASE_URL\n");

  console.log("Phase A: brands");
  const brands = await migrateBrands();
  console.log(
    `  brands: inserted=${brands.inserted} skipped=${brands.skipped} errors=${brands.errors}`
  );

  console.log("\nPhase B: catalog_products -> knowledge_items");
  const products = await migrateProducts(brands.brandIdByName, brands.manufacturersById);
  console.log(
    `  products: inserted=${products.inserted} skipped=${products.skipped} errors=${products.errors} (of ${products.total})`
  );

  console.log("\nPhase C: catalog_accessories (log-only)");
  logAccessories();

  console.log("\nPhase D: spec_sheet_records — skipped entirely (empty table)");

  console.log(`\nDONE. Brands inserted: ${brands.inserted}. Products migrated: ${products.inserted}. Errors: ${brands.errors + products.errors}.`);
  sqlite.close();
}

main().catch((e) => {
  console.error(e);
  sqlite.close();
  process.exit(1);
});
