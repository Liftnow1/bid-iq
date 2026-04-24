#!/usr/bin/env node
// One-shot migration: legacy Mohawk JSON extractions -> knowledge_items
//
// Sources:
//   kb_extracted/individual/*.json       (~199 per-PDF extractions)
//   kb_output/*.json                     (~37 enrich.py outputs)
//   kb_extracted/operation-manuals.json  (list of operation-manual extractions)
//
// Idempotent: dedupe by (source, title). The Mohawk brand row must exist
// (created by migrate-catalog-db.mjs OR created here on demand).
//
// Usage: DATABASE_URL=postgres://... node scripts/migrate-mohawk-jsons.mjs

import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { neon } from "@neondatabase/serverless";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const IND_DIR = path.join(REPO_ROOT, "kb_extracted", "individual");
const OUT_DIR = path.join(REPO_ROOT, "kb_output");
const OP_MANUALS_FILE = path.join(REPO_ROOT, "kb_extracted", "operation-manuals.json");

if (!process.env.DATABASE_URL) {
  console.error("DATABASE_URL not set. Aborting.");
  process.exit(1);
}
const sql = neon(process.env.DATABASE_URL);

async function ensureMohawkBrandId() {
  const rows = await sql`SELECT id FROM brands WHERE name = ${"Mohawk"} LIMIT 1`;
  if (rows.length > 0) return rows[0].id;
  const ins = await sql`
    INSERT INTO brands (name, manufacturer_name, relationship_type, we_carry)
    VALUES (${"Mohawk"}, ${"Mohawk"}, ${"own_vendor"}, ${true})
    ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
    RETURNING id
  `;
  return ins[0].id;
}

function inferCategory(filename, data) {
  const fn = (filename || "").toLowerCase();
  const docType = String(data.document_type || "").toLowerCase();
  const cat = String(data.category || "").toLowerCase();
  const hasInstallReq = data.installation_requirements && (
    Array.isArray(data.installation_requirements)
      ? data.installation_requirements.length > 0
      : Object.keys(data.installation_requirements || {}).length > 0
  );
  const hasPartsList = data.parts_list && (
    Array.isArray(data.parts_list) ? data.parts_list.length > 0 : Object.keys(data.parts_list || {}).length > 0
  );

  if (fn.includes("install") || docType.includes("install") || cat.includes("install") || hasInstallReq) {
    return "installation-guides";
  }
  if (fn.includes("parts") || hasPartsList) return "service-procedures";
  if (fn.includes("operation") || fn.includes("user") || fn.includes("manual") ||
      docType.includes("operation") || docType.includes("manual")) {
    return "service-procedures";
  }
  return "product-specifications";
}

function buildTitle(filename, data) {
  const model = data.model && String(data.model).trim();
  const variant = data.variant && String(data.variant).trim();
  if (model && variant) return `Mohawk ${model} (${variant})`;
  if (model) return `Mohawk ${model}`;
  if (filename) return filename.replace(/\.json$/i, "").replace(/[_-]+/g, " ").trim();
  return "Mohawk Document";
}

function buildSummary(data) {
  const docType = data.document_type || data.category || "Mohawk document";
  const model = data.model && String(data.model).trim();
  const capacity =
    (data.capacity && (typeof data.capacity === "string" ? data.capacity : JSON.stringify(data.capacity))) || null;
  const parts = [];
  if (model) parts.push(`Model ${model}`);
  if (capacity && String(capacity).trim() && String(capacity).trim() !== "{}") {
    parts.push(`Capacity: ${String(capacity).slice(0, 120)}`);
  }
  if (parts.length === 0) return `Mohawk ${docType}.`;
  return `Mohawk ${docType}. ${parts.join(", ")}.`;
}

function buildTags(data) {
  const tags = new Set(["mohawk"]);
  if (data.product_type) tags.add(String(data.product_type).toLowerCase().replace(/\s+/g, "-"));
  if (data.category) tags.add(String(data.category).toLowerCase().replace(/\s+/g, "-"));
  const certs = data.certifications;
  const certArr = Array.isArray(certs) ? certs : certs && typeof certs === "object" ? Object.values(certs).flat() : [];
  for (const c of certArr) {
    const s = String(c || "").toLowerCase();
    if (s.includes("ali")) tags.add("ali-certified");
    if (s.includes("etl")) tags.add("etl-listed");
    if (s.includes("ansi")) tags.add("ansi-compliant");
  }
  return Array.from(tags);
}

function relPath(absPath) {
  if (!absPath) return null;
  const clean = String(absPath).replace(/\\/g, "/");
  const idx = clean.indexOf("/data/product_data/");
  if (idx !== -1) return clean.slice(idx + 1); // strip leading slash
  return clean;
}

async function existsByNaturalKey(source, title) {
  const rows = await sql`
    SELECT id FROM knowledge_items
    WHERE source = ${source} AND title = ${title}
    LIMIT 1
  `;
  return rows.length > 0;
}

async function insertItem({
  title, category, tags, rawContent, summary, sourcePath,
  extractorVersion, brandId, source, extractedAt, pagesCount, sourceFilename,
}) {
  const searchText = [title, summary, ...tags, String(rawContent).slice(0, 6000)].filter(Boolean).join(" ");
  await sql`
    INSERT INTO knowledge_items (
      title, category, subcategory, tags, content_type, source,
      source_filename, raw_content, extracted_data, summary, search_text,
      brand_id, source_path, source_pages_count, extracted_at, extractor_version
    ) VALUES (
      ${title}, ${category}, ${null}, ${tags}, ${"json"}, ${source},
      ${sourceFilename || null}, ${rawContent}, ${null}, ${summary}, ${searchText},
      ${brandId}, ${sourcePath}, ${pagesCount}, ${extractedAt}, ${extractorVersion}
    )
  `;
}

async function migrateDir({ dir, source, extractorVersion, brandId }) {
  let inserted = 0, skipped = 0, errors = 0;
  const files = (await readdir(dir)).filter((f) => f.toLowerCase().endsWith(".json"));
  for (const fname of files) {
    const full = path.join(dir, fname);
    try {
      const raw = await readFile(full, "utf8");
      const data = JSON.parse(raw);
      const title = buildTitle(fname, data);
      if (await existsByNaturalKey(source, title)) { skipped++; continue; }
      const category = inferCategory(fname, data);
      const tags = buildTags(data);
      const summary = buildSummary(data);
      const pretty = JSON.stringify(data, null, 2);
      const sourceFieldRaw = data.source_file || data._source_file || null;
      const sourcePath = relPath(sourceFieldRaw);
      const sourceFilename = sourceFieldRaw ? path.basename(String(sourceFieldRaw)) : fname;
      const extractedAt = data._extracted_at ? new Date(data._extracted_at) : null;
      const pagesCount = typeof data._pages_count === "number" ? data._pages_count : null;

      await insertItem({
        title, category, tags, rawContent: pretty, summary, sourcePath,
        extractorVersion, brandId, source, extractedAt, pagesCount, sourceFilename,
      });
      inserted++;
    } catch (e) {
      errors++;
      console.error(`  ${fname}: ${e.message}`);
    }
  }
  return { inserted, skipped, errors, total: files.length };
}

async function migrateOperationManuals({ brandId }) {
  const source = "mohawk-json-migration";
  const extractorVersion = "scripts/extract-lfs-pdfs-mjs";
  let inserted = 0, skipped = 0, errors = 0;
  let raw;
  try { raw = await readFile(OP_MANUALS_FILE, "utf8"); } catch { return { inserted, skipped, errors, total: 0 }; }
  const arr = JSON.parse(raw);
  if (!Array.isArray(arr)) return { inserted, skipped, errors, total: 0 };
  const titleCounts = new Map();

  for (const data of arr) {
    try {
      const sourceFieldRaw = data.source_file || null;
      const filename = sourceFieldRaw
        ? path.basename(String(sourceFieldRaw)).replace(/\.pdf$/i, "")
        : null;
      let title = buildTitle(filename || "", data);
      const n = (titleCounts.get(title) || 0) + 1;
      titleCounts.set(title, n);
      if (n > 1) title = `${title} [${n}]`;

      if (await existsByNaturalKey(source, title)) { skipped++; continue; }

      const category = inferCategory(filename || "", data);
      const tags = buildTags(data);
      const summary = buildSummary(data);
      const pretty = JSON.stringify(data, null, 2);
      const sourcePath = relPath(sourceFieldRaw);
      const sourceFilename = sourceFieldRaw ? path.basename(String(sourceFieldRaw)) : null;

      await insertItem({
        title, category, tags, rawContent: pretty, summary, sourcePath,
        extractorVersion, brandId, source, extractedAt: null, pagesCount: null, sourceFilename,
      });
      inserted++;
    } catch (e) {
      errors++;
      console.error(`  op-manual entry: ${e.message}`);
    }
  }
  return { inserted, skipped, errors, total: arr.length };
}

async function main() {
  const brandId = await ensureMohawkBrandId();
  console.log(`Mohawk brand_id = ${brandId}\n`);

  console.log("A) kb_output/*.json  (enrich.py-legacy)");
  const rOut = await migrateDir({
    dir: OUT_DIR,
    source: "mohawk-json-migration",
    extractorVersion: "enrich.py-legacy",
    brandId,
  });
  console.log(`   inserted=${rOut.inserted} skipped=${rOut.skipped} errors=${rOut.errors} of ${rOut.total}`);

  console.log("\nB) kb_extracted/individual/*.json  (scripts/extract-lfs-pdfs-mjs)");
  const rInd = await migrateDir({
    dir: IND_DIR,
    source: "mohawk-json-migration",
    extractorVersion: "scripts/extract-lfs-pdfs-mjs",
    brandId,
  });
  console.log(`   inserted=${rInd.inserted} skipped=${rInd.skipped} errors=${rInd.errors} of ${rInd.total}`);

  console.log("\nC) kb_extracted/operation-manuals.json");
  const rOp = await migrateOperationManuals({ brandId });
  console.log(`   inserted=${rOp.inserted} skipped=${rOp.skipped} errors=${rOp.errors} of ${rOp.total}`);

  const total = rOut.inserted + rInd.inserted + rOp.inserted;
  const errors = rOut.errors + rInd.errors + rOp.errors;
  console.log(`\nDONE. Total inserted: ${total}. Errors: ${errors}.`);
}

main().catch((e) => { console.error(e); process.exit(1); });
