import { NextResponse } from "next/server";
import { getSQL, ensureSchema } from "@/lib/db";

export async function POST() {
  try {
    await ensureSchema();
    const sql = getSQL();

    const brandsCount = await sql`SELECT count(*)::int as count FROM brands`;
    const kiCount = await sql`SELECT count(*)::int as count FROM knowledge_items`;

    return NextResponse.json({
      status: "ok",
      message: "Schema ensured",
      brands: brandsCount[0].count,
      knowledge_items: kiCount[0].count,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
