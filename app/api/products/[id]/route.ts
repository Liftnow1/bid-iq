// GET /api/products/[id] — full product detail with all matched documents.
//
// Response: { product: {...full product fields...}, documents: [...] }

import { NextRequest, NextResponse } from "next/server";
import { getSQL, ensureSchema } from "@/lib/db";

export const maxDuration = 30;

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    await ensureSchema();
    const sql = getSQL();
    const { id } = await params;
    const productId = Number(id);
    if (!Number.isInteger(productId) || productId < 1) {
      return NextResponse.json({ error: "Invalid product id" }, { status: 400 });
    }

    const rows = (await sql`
      SELECT p.id, p.brand_id, b.name AS brand_name, p.sku, p.family_name,
             p.product_name, p.description, p.category, p.capacity_lbs,
             p.is_ali_certified, p.ali_cert_date,
             coalesce(p.variant_skus, '[]'::jsonb) AS variant_skus,
             p.status, p.source, p.source_file, p.notes,
             p.image_url, p.image_source_url, p.image_fetched_at,
             p.created_at, p.updated_at
      FROM products p JOIN brands b ON b.id = p.brand_id
      WHERE p.id = ${productId}
      LIMIT 1
    `) as unknown as Array<Record<string, unknown>>;
    if (rows.length === 0) {
      return NextResponse.json({ error: "Product not found" }, { status: 404 });
    }
    const product = rows[0];

    const docs = (await sql`
      SELECT pd.id, pd.product_id, pd.knowledge_item_id, pd.doc_type,
             pd.is_primary, pd.pdf_url, pd.notes, pd.created_at,
             ki.title AS ki_title, ki.source_filename AS ki_filename,
             ki.source_path AS ki_source_path,
             length(coalesce(ki.raw_content,'')) AS ki_body_chars
      FROM product_documents pd
      LEFT JOIN knowledge_items ki ON ki.id = pd.knowledge_item_id
      WHERE pd.product_id = ${productId}
      ORDER BY pd.is_primary DESC, pd.doc_type, pd.id
    `) as unknown as Array<Record<string, unknown>>;

    return NextResponse.json({ product, documents: docs });
  } catch (err) {
    console.error("/api/products/[id] error:", err);
    const msg = err instanceof Error ? err.message : "Failed to query product";
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
