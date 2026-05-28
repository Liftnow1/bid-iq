import { NextRequest, NextResponse } from "next/server";
import { getSQL, ensureSchema } from "@/lib/db";

/**
 * /api/self-reject-log — log of drafts that an agent's own self-reject
 * gate rejected before they reached HubSpot. Replaces the prior in-memory
 * staticData-only logging that was invisible outside n8n.
 *
 * POST  body { agent, piece_type, url, title, draft_preview, word_count,
 *              failed_checks (required, array), checks_full, n8n_execution_id,
 *              source_handoff_id }
 *       → 200 {ok:true, id, created_at}
 *
 * GET   ?agent=Content%20Producer&limit=50
 *       → 200 {ok:true, count, rows:[...]}
 *
 * GET   ?aggregate=top-failed-checks&days=7
 *       → 200 {ok:true, period_days, top:[{check, count}]}
 *
 * AUTH: x-agent-secret header
 */

export const maxDuration = 15;
export const runtime = "nodejs";

const SHARED_SECRET = "LiftnowAgentTeam_kS9-bGq2_xVtN4mP";

function unauthorized() {
  return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
}

function authOk(req: NextRequest) {
  return (req.headers.get("x-agent-secret") || "") === SHARED_SECRET;
}

export async function POST(req: NextRequest) {
  if (!authOk(req)) return unauthorized();
  try {
    await ensureSchema();
    const body = await req.json().catch(() => ({}));
    const agent = String(body?.agent || "").trim();
    const failed = body?.failed_checks;
    if (!agent || !Array.isArray(failed)) {
      return NextResponse.json(
        { ok: false, error: "agent + failed_checks[] required" },
        { status: 400 }
      );
    }
    const piece_type = String(body?.piece_type || "").slice(0, 50);
    const url = String(body?.url || "").slice(0, 500);
    const title = String(body?.title || "").slice(0, 500);
    const draft_preview = String(body?.draft_preview || "").slice(0, 8000);
    const word_count = Number.isFinite(body?.word_count) ? Math.floor(body.word_count) : null;
    const checks_full = body?.checks_full ?? null;
    const exec_id = String(body?.n8n_execution_id || "").slice(0, 100);
    const handoff_id = Number.isFinite(body?.source_handoff_id)
      ? Math.floor(body.source_handoff_id)
      : null;

    const sql = getSQL();
    const inserted = (await sql`
      INSERT INTO self_reject_log
        (agent, piece_type, url, title, draft_preview, word_count,
         failed_checks, checks_full, n8n_execution_id, source_handoff_id)
      VALUES
        (${agent}, ${piece_type || null}, ${url || null}, ${title || null},
         ${draft_preview || null}, ${word_count},
         ${JSON.stringify(failed)}, ${checks_full ? JSON.stringify(checks_full) : null},
         ${exec_id || null}, ${handoff_id})
      RETURNING id, created_at
    `) as Array<{ id: string; created_at: string }>;

    return NextResponse.json({
      ok: true,
      id: inserted[0].id,
      created_at: inserted[0].created_at,
    });
  } catch (e: any) {
    // The (n8n_execution_id, url) unique index rejected a duplicate write — a
    // second self-reject from the same execution. Treat as success (idempotent),
    // not a 500, so a retried/duplicate fire is a no-op rather than an error.
    if (e?.code === "23505" || /duplicate key|unique constraint/i.test(String(e?.message || ""))) {
      return NextResponse.json({ ok: true, deduped: true });
    }
    return NextResponse.json(
      { ok: false, error: String(e?.message || e).slice(0, 500) },
      { status: 500 }
    );
  }
}

export async function GET(req: NextRequest) {
  if (!authOk(req)) return unauthorized();
  try {
    await ensureSchema();
    const sp = req.nextUrl.searchParams;
    const aggregate = sp.get("aggregate");
    const agent = (sp.get("agent") || "").trim();
    const sql = getSQL();

    if (aggregate === "top-failed-checks") {
      const days = Math.min(Math.max(parseInt(sp.get("days") || "7", 10) || 7, 1), 90);
      // Unnest the JSONB array of failed checks, group + count.
      const rows = (await sql`
        SELECT check_name, COUNT(*)::int AS count
        FROM (
          SELECT jsonb_array_elements_text(failed_checks) AS check_name
          FROM self_reject_log
          WHERE created_at > NOW() - (${days} * INTERVAL '1 day')
        ) t
        GROUP BY check_name
        ORDER BY count DESC
        LIMIT 50
      `) as Array<{ check_name: string; count: number }>;
      return NextResponse.json({
        ok: true,
        period_days: days,
        top: rows.map((r) => ({ check: r.check_name, count: r.count })),
      });
    }

    const limit = Math.min(parseInt(sp.get("limit") || "50", 10) || 50, 500);
    let rows;
    if (agent) {
      rows = await sql`
        SELECT id, agent, piece_type, url, title, draft_preview, word_count,
               failed_checks, n8n_execution_id, source_handoff_id, created_at
        FROM self_reject_log
        WHERE agent = ${agent}
        ORDER BY created_at DESC
        LIMIT ${limit}
      `;
    } else {
      rows = await sql`
        SELECT id, agent, piece_type, url, title, draft_preview, word_count,
               failed_checks, n8n_execution_id, source_handoff_id, created_at
        FROM self_reject_log
        ORDER BY created_at DESC
        LIMIT ${limit}
      `;
    }
    return NextResponse.json({ ok: true, count: (rows as any[]).length, rows });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: String(e?.message || e).slice(0, 500) },
      { status: 500 }
    );
  }
}
