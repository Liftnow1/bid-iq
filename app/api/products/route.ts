// GET /api/products — paginated product catalog for the inventory portal.
//
// Query params:
//   brand       (string, optional) — filter to one brand by name (case-insensitive)
//   brands      (string, optional) — comma-separated list of brand names
//   category    (string, optional) — filter to one of the 10 lift categories
//   categories  (string, optional) — comma-separated list of categories
//   capacity_min (int, optional)   — minimum capacity_lbs
//   capacity_max (int, optional)   — maximum capacity_lbs
//   status      (string, optional) — current | discontinued | unknown (default: current)
//   sku         (string, optional) — exact SKU match (family OR variant)
//   q           (string, optional) — free-text ILIKE on family_name + product_name + description
//   page        (int, optional)    — 1-indexed; default 1
//   page_size   (int, optional)    — default 50; max 200
//   include_documents (bool)       — also return matched docs per product (default false)
//
// Response:
//   {
//     products: [...],
//     total: <int>, page: <int>, page_size: <int>, total_pages: <int>
//   }
//
// Sort: brand, category, capacity_lbs, sku.

import { NextRequest, NextResponse } from "next/server";
import { getSQL, ensureSchema } from "@/lib/db";

export const maxDuration = 30;

type ProductRow = {
  id: number;
  brand_id: number;
  brand_name: string;
  sku: string;
  family_name: string | null;
  product_name: string | null;
  description: string | null;
  category: string;
  capacity_lbs: number | null;
  is_ali_certified: boolean;
  ali_cert_date: string | null;
  variant_skus: string[];
  status: string;
  source: string;
  source_file: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

type ProductDocumentRow = {
  id: number;
  product_id: number;
  knowledge_item_id: number | null;
  doc_type: string;
  is_primary: boolean;
  pdf_url: string | null;
  ki_title: string | null;
  ki_filename: string | null;
};

const VALID_CATEGORIES = new Set([
  "two-post-lift", "four-post-lift", "scissor-lift", "mobile-column",
  "light-duty-inground", "heavy-duty-inground", "vertical-rise-lift",
  "parallelogram-lift", "low-rise-lift", "rolling-jack", "unclassified",
]);
const VALID_STATUSES = new Set(["current", "discontinued", "unknown"]);

function parseList(s: string | null): string[] {
  if (!s) return [];
  return s.split(",").map((x) => x.trim()).filter(Boolean);
}

function parsePosInt(s: string | null, fallback: number, max?: number): number {
  if (s === null || s === "") return fallback;
  const n = Number(s);
  if (!Number.isInteger(n) || n < 1) return fallback;
  if (max !== undefined && n > max) return max;
  return n;
}

export async function GET(request: NextRequest) {
  try {
    await ensureSchema();
    const sql = getSQL();
    const sp = request.nextUrl.searchParams;

    // Filters
    const brandSingle = sp.get("brand");
    const brandsList = parseList(sp.get("brands"));
    const brands = brandSingle ? [brandSingle, ...brandsList] : brandsList;
    const brandsLower = brands.map((b) => b.toLowerCase());

    const catSingle = sp.get("category");
    const catsList = parseList(sp.get("categories"));
    const categories = (catSingle ? [catSingle, ...catsList] : catsList).filter(
      (c) => VALID_CATEGORIES.has(c)
    );

    const capMin = sp.get("capacity_min") ? Number(sp.get("capacity_min")) : null;
    const capMax = sp.get("capacity_max") ? Number(sp.get("capacity_max")) : null;
    const capMinValid = capMin !== null && Number.isFinite(capMin) ? capMin : null;
    const capMaxValid = capMax !== null && Number.isFinite(capMax) ? capMax : null;

    let status = sp.get("status");
    if (status && !VALID_STATUSES.has(status)) status = null;
    const skuExact = sp.get("sku");
    const q = (sp.get("q") || "").trim();
    const page = parsePosInt(sp.get("page"), 1);
    const pageSize = parsePosInt(sp.get("page_size"), 50, 200);
    const offset = (page - 1) * pageSize;
    const includeDocuments = sp.get("include_documents") === "true";

    // Single dynamic SQL for both count and page. Inline `${var}` Neon
    // serverless template parameters are positional; keep them readable.
    const brandsArr = brandsLower.length > 0 ? brandsLower : null;
    const catsArr = categories.length > 0 ? categories : null;

    // We do count + page in two calls; cheaper than a window + over() for
    // larger result sets. The filter expression is identical between them.
    const countRows = (await sql`
      SELECT count(*)::int AS n
      FROM products p JOIN brands b ON b.id = p.brand_id
      WHERE 1=1
        AND (${brandsArr}::text[] IS NULL OR lower(b.name) = ANY(${brandsArr}::text[]))
        AND (${catsArr}::text[] IS NULL OR p.category = ANY(${catsArr}::text[]))
        AND (${capMinValid}::int IS NULL OR p.capacity_lbs >= ${capMinValid}::int)
        AND (${capMaxValid}::int IS NULL OR p.capacity_lbs <= ${capMaxValid}::int)
        AND (${status}::text IS NULL OR p.status = ${status}::text)
        AND (${skuExact}::text IS NULL OR lower(p.sku) = lower(${skuExact}::text)
             OR p.variant_skus @> to_jsonb(${skuExact}::text))
        AND (${q}::text = '' OR (
             coalesce(p.family_name,'') ILIKE '%' || ${q} || '%'
          OR coalesce(p.product_name,'') ILIKE '%' || ${q} || '%'
          OR coalesce(p.description,'') ILIKE '%' || ${q} || '%'
          OR p.sku ILIKE '%' || ${q} || '%'
        ))
    `) as unknown as Array<{ n: number }>;
    const total = countRows[0]?.n ?? 0;

    const rows = (await sql`
      SELECT p.id, p.brand_id, b.name AS brand_name, p.sku, p.family_name,
             p.product_name, p.description, p.category, p.capacity_lbs,
             p.is_ali_certified, p.ali_cert_date,
             coalesce(p.variant_skus, '[]'::jsonb) AS variant_skus,
             p.status, p.source, p.source_file, p.notes,
             p.image_url, p.image_source_url, p.image_fetched_at,
             p.created_at, p.updated_at
      FROM products p JOIN brands b ON b.id = p.brand_id
      WHERE 1=1
        AND (${brandsArr}::text[] IS NULL OR lower(b.name) = ANY(${brandsArr}::text[]))
        AND (${catsArr}::text[] IS NULL OR p.category = ANY(${catsArr}::text[]))
        AND (${capMinValid}::int IS NULL OR p.capacity_lbs >= ${capMinValid}::int)
        AND (${capMaxValid}::int IS NULL OR p.capacity_lbs <= ${capMaxValid}::int)
        AND (${status}::text IS NULL OR p.status = ${status}::text)
        AND (${skuExact}::text IS NULL OR lower(p.sku) = lower(${skuExact}::text)
             OR p.variant_skus @> to_jsonb(${skuExact}::text))
        AND (${q}::text = '' OR (
             coalesce(p.family_name,'') ILIKE '%' || ${q} || '%'
          OR coalesce(p.product_name,'') ILIKE '%' || ${q} || '%'
          OR coalesce(p.description,'') ILIKE '%' || ${q} || '%'
          OR p.sku ILIKE '%' || ${q} || '%'
        ))
      ORDER BY b.name, p.category, p.capacity_lbs NULLS LAST, p.sku
      LIMIT ${pageSize} OFFSET ${offset}
    `) as unknown as ProductRow[];

    // Optional join: documents per product on this page
    let docsByProduct: Record<number, ProductDocumentRow[]> = {};
    if (includeDocuments && rows.length > 0) {
      const ids = rows.map((r) => r.id);
      const docs = (await sql`
        SELECT pd.id, pd.product_id, pd.knowledge_item_id, pd.doc_type,
               pd.is_primary, pd.pdf_url,
               ki.title AS ki_title, ki.source_filename AS ki_filename
        FROM product_documents pd
        LEFT JOIN knowledge_items ki ON ki.id = pd.knowledge_item_id
        WHERE pd.product_id = ANY(${ids}::int[])
        ORDER BY pd.product_id, pd.is_primary DESC, pd.doc_type, pd.id
      `) as unknown as ProductDocumentRow[];
      for (const d of docs) {
        (docsByProduct[d.product_id] ??= []).push(d);
      }
    }

    const products = rows.map((r) => ({
      ...r,
      documents: includeDocuments ? (docsByProduct[r.id] ?? []) : undefined,
    }));

    return NextResponse.json({
      products,
      total,
      page,
      page_size: pageSize,
      total_pages: total === 0 ? 0 : Math.ceil(total / pageSize),
    });
  } catch (err) {
    console.error("/api/products error:", err);
    const msg = err instanceof Error ? err.message : "Failed to query products";
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
