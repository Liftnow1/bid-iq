import { NextRequest, NextResponse } from "next/server";
import { getSQL, ensureSchema } from "@/lib/db";

export async function GET(request: NextRequest) {
  try {
    await ensureSchema();
    const sql = getSQL();

    const { searchParams } = new URL(request.url);
    const category = searchParams.get("category");
    const search = searchParams.get("search");
    const limit = Math.min(parseInt(searchParams.get("limit") || "50"), 100);
    const offset = parseInt(searchParams.get("offset") || "0");

    let items;

    if (search) {
      const tsQuery = search
        .toLowerCase()
        .replace(/[^a-z0-9\s]/g, " ")
        .split(/\s+/)
        .filter((w) => w.length > 1)
        .join(" | ");

      items = await sql`
        SELECT id, title, category, subcategory, tags, content_type, source,
               source_filename, summary, created_at
        FROM knowledge_items
        WHERE to_tsvector('english', search_text) @@ to_tsquery('english', ${tsQuery})
        ORDER BY created_at DESC
        LIMIT ${limit} OFFSET ${offset}
      `;
    } else if (category) {
      items = await sql`
        SELECT id, title, category, subcategory, tags, content_type, source,
               source_filename, summary, created_at
        FROM knowledge_items
        WHERE category = ${category}
        ORDER BY created_at DESC
        LIMIT ${limit} OFFSET ${offset}
      `;
    } else {
      items = await sql`
        SELECT id, title, category, subcategory, tags, content_type, source,
               source_filename, summary, created_at
        FROM knowledge_items
        ORDER BY created_at DESC
        LIMIT ${limit} OFFSET ${offset}
      `;
    }

    // Get category counts
    const counts = await sql`
      SELECT category, count(*)::int as count
      FROM knowledge_items
      GROUP BY category
      ORDER BY count DESC
    `;

    const total = await sql`SELECT count(*)::int as total FROM knowledge_items`;

    return NextResponse.json({
      items,
      categories: counts,
      total: total[0].total,
    });
  } catch (err) {
    console.error("Knowledge base list error:", err);
    const message = err instanceof Error ? err.message : "Failed to list items";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function DELETE(request: NextRequest) {
  try {
    await ensureSchema();
    const sql = getSQL();

    const { id } = await request.json();
    if (!id) {
      return NextResponse.json({ error: "No id provided" }, { status: 400 });
    }

    await sql`DELETE FROM knowledge_items WHERE id = ${id}`;
    return NextResponse.json({ success: true });
  } catch (err) {
    console.error("Knowledge base delete error:", err);
    const message = err instanceof Error ? err.message : "Failed to delete item";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
