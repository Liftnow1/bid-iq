import { NextRequest, NextResponse } from "next/server";
import { getSQL, ensureSchema } from "@/lib/db";

/**
 * /api/agent-handoffs — typed cross-agent handoff contract.
 *
 * Replaces the legacy `HANDOFF:{json}` marker that was embedded in HubSpot
 * ticket outcome_notes via regex (fragile, no validation, no consumed
 * tracking, can't extend to new agent pairs without per-pair code).
 *
 * AUTH: shared agent secret via `x-agent-secret` header — same secret used
 * everywhere else on the agent stack (`LiftnowAgentTeam_kS9-bGq2_xVtN4mP`).
 *
 * ENDPOINTS (this file):
 *   POST  body { from_agent, to_agent, kind, payload, source_ticket_id }
 *         → 200 {ok:true, id, created} on insert
 *         → 200 {ok:true, id, existing} if (source_ticket_id, kind) exists
 *         → 400 on missing/invalid fields
 *
 *   GET   ?to_agent=Owl&pending=true&limit=10
 *         → 200 {ok:true, handoffs:[...]}
 *         pending=true filters to consumed_at IS NULL.
 *         Default limit 50, max 200.
 *
 *   POST /:id/consume (see app/api/agent-handoffs/[id]/consume/route.ts)
 *         → 200 {ok:true, id, was_already_consumed}
 *
 * Allowed `kind` values (extend as needed):
 *   - refresh_url  (Content Decay Detector → Content Producer)
 *   - new_keyword  (Keyword Discovery → SEM Manager, future)
 *   - serp_alert   (Competitor SERP → Coordinator, future)
 */

export const maxDuration = 15;
export const runtime = "nodejs";

const SHARED_SECRET = "LiftnowAgentTeam_kS9-bGq2_xVtN4mP";

const ALLOWED_KINDS = new Set(["refresh_url", "new_keyword", "serp_alert", "brand_mention"]);
const ALLOWED_TO_AGENTS = new Set([
  "Content Producer", "SEO Optimizer", "SEM Manager", "Coordinator",
  "UI/UX Performance", "Backlink Builder", "Brand Listening", "LinkedIn Cadence",
]);

function unauthorized() {
  return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
}

function authOk(req: NextRequest) {
  const got = req.headers.get("x-agent-secret") || "";
  return got === SHARED_SECRET;
}

export async function POST(req: NextRequest) {
  if (!authOk(req)) return unauthorized();
  try {
    await ensureSchema();
    const body = await req.json().catch(() => ({}));

    const from_agent = String(body?.from_agent || "").trim();
    const to_agent = String(body?.to_agent || "").trim();
    const kind = String(body?.kind || "").trim();
    const source_ticket_id = String(body?.source_ticket_id || "").trim();
    const payload = body?.payload;

    if (!from_agent || !to_agent || !kind || !source_ticket_id) {
      return NextResponse.json({
        ok: false,
        error: "missing required: from_agent, to_agent, kind, source_ticket_id",
      }, { status: 400 });
    }
    if (!ALLOWED_KINDS.has(kind)) {
      return NextResponse.json({
        ok: false,
        error: `kind must be one of: ${[...ALLOWED_KINDS].join(", ")}`,
      }, { status: 400 });
    }
    if (!ALLOWED_TO_AGENTS.has(to_agent)) {
      return NextResponse.json({
        ok: false,
        error: `to_agent must be one of: ${[...ALLOWED_TO_AGENTS].join(", ")}`,
      }, { status: 400 });
    }
    if (typeof payload !== "object" || payload === null) {
      return NextResponse.json({
        ok: false,
        error: "payload must be an object",
      }, { status: 400 });
    }

    const sql = getSQL();
    // ON CONFLICT (source_ticket_id, kind) DO NOTHING — idempotent for AE re-runs
    const inserted = (await sql`
      INSERT INTO agent_handoffs (from_agent, to_agent, kind, payload, source_ticket_id)
      VALUES (${from_agent}, ${to_agent}, ${kind}, ${JSON.stringify(payload)}, ${source_ticket_id})
      ON CONFLICT (source_ticket_id, kind) DO NOTHING
      RETURNING id, created_at
    `) as Array<{ id: number; created_at: string }>;

    if (inserted.length > 0) {
      return NextResponse.json({
        ok: true,
        id: inserted[0].id,
        created_at: inserted[0].created_at,
        action: "created",
      });
    }

    // Already exists — return the existing row id
    const existing = (await sql`
      SELECT id, created_at FROM agent_handoffs
      WHERE source_ticket_id = ${source_ticket_id} AND kind = ${kind}
      LIMIT 1
    `) as Array<{ id: number; created_at: string }>;

    return NextResponse.json({
      ok: true,
      id: existing[0]?.id ?? null,
      created_at: existing[0]?.created_at ?? null,
      action: "existing",
    });
  } catch (e: any) {
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
    const to_agent = (sp.get("to_agent") || "").trim();
    const pending = sp.get("pending") === "true";
    const limit = Math.min(parseInt(sp.get("limit") || "50", 10) || 50, 200);

    const sql = getSQL();
    let rows;
    if (to_agent && pending) {
      rows = await sql`
        SELECT id, from_agent, to_agent, kind, payload, source_ticket_id,
               created_at, consumed_at, consumed_by_execution_id, result
        FROM agent_handoffs
        WHERE to_agent = ${to_agent} AND consumed_at IS NULL
        ORDER BY created_at ASC
        LIMIT ${limit}
      `;
    } else if (to_agent) {
      rows = await sql`
        SELECT id, from_agent, to_agent, kind, payload, source_ticket_id,
               created_at, consumed_at, consumed_by_execution_id, result
        FROM agent_handoffs
        WHERE to_agent = ${to_agent}
        ORDER BY created_at DESC
        LIMIT ${limit}
      `;
    } else if (pending) {
      rows = await sql`
        SELECT id, from_agent, to_agent, kind, payload, source_ticket_id,
               created_at, consumed_at, consumed_by_execution_id, result
        FROM agent_handoffs
        WHERE consumed_at IS NULL
        ORDER BY created_at ASC
        LIMIT ${limit}
      `;
    } else {
      rows = await sql`
        SELECT id, from_agent, to_agent, kind, payload, source_ticket_id,
               created_at, consumed_at, consumed_by_execution_id, result
        FROM agent_handoffs
        ORDER BY created_at DESC
        LIMIT ${limit}
      `;
    }

    return NextResponse.json({
      ok: true,
      count: (rows as any[]).length,
      handoffs: rows,
    });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: String(e?.message || e).slice(0, 500) },
      { status: 500 }
    );
  }
}
