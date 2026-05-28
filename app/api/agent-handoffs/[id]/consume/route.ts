import { NextRequest, NextResponse } from "next/server";
import { getSQL, ensureSchema } from "@/lib/db";

/**
 * POST /api/agent-handoffs/:id/consume
 *
 * Mark a handoff as consumed by a specific n8n execution. Idempotent: if
 * the row is already consumed, returns the existing consumed_at + 200 OK.
 *
 * Body: { execution_id?: string, result?: string }
 *
 * AUTH: x-agent-secret header
 */

export const maxDuration = 10;
export const runtime = "nodejs";

const SHARED_SECRET = "LiftnowAgentTeam_kS9-bGq2_xVtN4mP";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id: idParam } = await params;
  const id = parseInt(idParam || "", 10);
  if (!id || isNaN(id)) {
    return NextResponse.json({ ok: false, error: "invalid id" }, { status: 400 });
  }
  if (req.headers.get("x-agent-secret") !== SHARED_SECRET) {
    return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }
  try {
    await ensureSchema();
    const body = await req.json().catch(() => ({}));
    const exec_id = String(body?.execution_id || "").slice(0, 100);
    const result = String(body?.result || "consumed").slice(0, 500);

    const sql = getSQL();
    // Only update if not already consumed (idempotent)
    const updated = (await sql`
      UPDATE agent_handoffs
      SET consumed_at = NOW(),
          consumed_by_execution_id = ${exec_id || null},
          result = ${result}
      WHERE id = ${id} AND consumed_at IS NULL
      RETURNING id, consumed_at, result
    `) as Array<{ id: number; consumed_at: string; result: string }>;

    if (updated.length > 0) {
      return NextResponse.json({
        ok: true,
        id: updated[0].id,
        consumed_at: updated[0].consumed_at,
        result: updated[0].result,
        was_already_consumed: false,
      });
    }

    // Was already consumed — return existing row's state
    const existing = (await sql`
      SELECT id, consumed_at, consumed_by_execution_id, result
      FROM agent_handoffs
      WHERE id = ${id}
    `) as Array<{ id: number; consumed_at: string | null; consumed_by_execution_id: string | null; result: string | null }>;

    if (existing.length === 0) {
      return NextResponse.json({ ok: false, error: "handoff not found" }, { status: 404 });
    }

    return NextResponse.json({
      ok: true,
      id: existing[0].id,
      consumed_at: existing[0].consumed_at,
      consumed_by_execution_id: existing[0].consumed_by_execution_id,
      result: existing[0].result,
      was_already_consumed: existing[0].consumed_at !== null,
    });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: String(e?.message || e).slice(0, 500) },
      { status: 500 }
    );
  }
}
