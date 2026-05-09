// GET /api/products/[id]/documents — just the documents linked to a
// product. Useful when the portal already has the product record and
// only needs to refresh the doc list / download URLs.
//
// Response: { documents: [...] }

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

    // Confirm the product exists so the portal gets a clean 404 instead of
    // an empty array on a typo'd id.
    const exists = (await sql`SELECT 1 FROM products WHERE id = ${productId} LIMIT 1`) as unknown as Array<{ "?column?": number }>;
    if (exists.length === 0) {
      return NextResponse.json({ error: "Product not found" }, { status: 404 });
    }

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

    return NextResponse.json({ documents: docs });
  } catch (err) {
    console.error("/api/products/[id]/documents error:", err);
    const msg = err instanceof Error ? err.message : "Failed to query documents";
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
