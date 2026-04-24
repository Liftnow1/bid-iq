import { NextRequest, NextResponse } from "next/server";
import { getSQL, ensureSchema } from "@/lib/db";
import { isTierTwo, runPythonUpgrade } from "@/lib/upgrade";

export const maxDuration = 300;

type KnowledgeRow = {
  id: number;
  title: string;
  category: string;
  summary: string | null;
  tags: string[] | null;
  raw_content: string | null;
  source_path: string | null;
  source_filename: string | null;
  extracted_at: string | null;
  extractor_version: string | null;
  extracted_data: Record<string, unknown> | null;
};

async function fetchRow(id: number): Promise<KnowledgeRow | null> {
  const sql = getSQL();
  const rows = (await sql`
    SELECT id, title, category, summary, tags, raw_content,
           source_path, source_filename, extracted_at, extractor_version,
           extracted_data
      FROM knowledge_items
     WHERE id = ${id}
     LIMIT 1
  `) as unknown as KnowledgeRow[];
  return rows[0] ?? null;
}

export async function POST(request: NextRequest) {
  try {
    await ensureSchema();

    const body = await request.json().catch(() => ({}));
    const id = Number(body?.knowledge_item_id);
    if (!Number.isInteger(id) || id <= 0) {
      return NextResponse.json(
        { error: "knowledge_item_id (positive integer) required" },
        { status: 400 }
      );
    }

    const before = await fetchRow(id);
    if (!before) {
      return NextResponse.json({ error: `knowledge_items.id=${id} not found` }, { status: 404 });
    }
    if (isTierTwo(before)) {
      return NextResponse.json({ status: "already_tier_2", item: before });
    }

    const result = await runPythonUpgrade(id);
    if (!result.ok) {
      return NextResponse.json(
        {
          status: "upgrade_failed",
          error: result.error,
          stdout: result.stdout,
          stderr: result.stderr,
        },
        { status: 500 }
      );
    }

    const after = await fetchRow(id);
    return NextResponse.json({
      status: "upgraded",
      item: after,
      stdout: result.stdout,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "upgrade failed";
    return NextResponse.json({ status: "upgrade_failed", error: message }, { status: 500 });
  }
}
