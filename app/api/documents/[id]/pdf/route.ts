// GET /api/documents/[id]/pdf
//
// Resolves a product_documents row to a clickable PDF and 302-redirects
// to it. The portal stores the absolute form of this URL in
// product_documents.pdf_url, so consumers don't have to know where the
// underlying file actually lives — the indirection lets us move PDFs
// between storage backends (today: Rotary's public S3; tomorrow:
// Cloudflare R2 for the rest of the brands) without rewriting every
// stored URL in the catalog.
//
// Lookup:
//   product_documents.id
//     -> knowledge_items.id (via knowledge_item_id)
//        -> knowledge_items.external_url
//   if external_url is non-null  -> 302 redirect to it
//   if external_url is null      -> 503 JSON with the filename + path so
//                                   the portal can render "PDF coming
//                                   soon" with useful context
//
// Today 1,444 of 1,694 Rotary/Forward KB items have external_url
// populated (399 product_documents resolve to clickable links). Other
// brands' PDFs aren't yet hosted publicly, so /api/documents/<id>/pdf
// returns 503 for them — that's the queue for the R2 upload pass.

import { NextRequest, NextResponse } from "next/server";
import { getSQL, ensureSchema } from "@/lib/db";

export const maxDuration = 10;

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    await ensureSchema();
    const sql = getSQL();
    const { id } = await params;
    const pdId = Number(id);
    if (!Number.isInteger(pdId) || pdId < 1) {
      return NextResponse.json({ error: "Invalid document id" }, { status: 400 });
    }

    const rows = (await sql`
      SELECT pd.id, pd.product_id, pd.doc_type,
             ki.id              AS ki_id,
             ki.source_filename AS ki_filename,
             ki.source_path     AS ki_source_path,
             ki.external_url    AS ki_external_url,
             ki.title           AS ki_title
      FROM product_documents pd
      LEFT JOIN knowledge_items ki ON ki.id = pd.knowledge_item_id
      WHERE pd.id = ${pdId}
      LIMIT 1
    `) as unknown as Array<Record<string, unknown>>;

    if (rows.length === 0) {
      return NextResponse.json({ error: "Document not found" }, { status: 404 });
    }
    const row = rows[0];

    const externalUrl = row.ki_external_url as string | null;
    if (externalUrl) {
      // 302 (Found) — the URL we're redirecting to is the canonical
      // location *right now*; if we migrate storage tomorrow the
      // pdf_url stays the same and 302s to the new home. Don't 301
      // (permanent) because that lets browsers cache the redirect and
      // pin it through a future storage migration.
      return NextResponse.redirect(externalUrl, 302);
    }

    return NextResponse.json(
      {
        error: "PDF not yet hosted publicly",
        document_id: row.id,
        product_id: row.product_id,
        doc_type: row.doc_type,
        ki_id: row.ki_id,
        ki_filename: row.ki_filename,
        ki_source_path: row.ki_source_path,
        ki_title: row.ki_title,
        message:
          "The underlying file exists in the bid-iq KB but hasn't been uploaded " +
          "to object storage yet. Rotary/Forward docs are live today; other " +
          "brands ship after the R2 upload pass.",
      },
      { status: 503 },
    );
  } catch (err) {
    console.error("/api/documents/[id]/pdf error:", err);
    const msg = err instanceof Error ? err.message : "Failed to resolve document";
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
