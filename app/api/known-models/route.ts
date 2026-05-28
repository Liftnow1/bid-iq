import { NextRequest, NextResponse } from "next/server";
import { getSQL } from "@/lib/db";

/**
 * GET /api/known-models?brands=bendpak,challenger,pks
 *
 * Returns the actual product model names Liftnow carries, extracted from
 * the knowledge_items table. Used by Bee (and any other content agent) to
 * GROUND its output — the LLM should ONLY reference models from this list,
 * never invent.
 *
 * Response shape:
 *   {
 *     ok: true,
 *     brands: {
 *       bendpak: { models: ["10AP", "EV4000SL", "RML-1500XL", ...], count: 482 },
 *       challenger: { models: ["VLE10", "LE12", "LE12-3S", "SX14", ...], count: 325 },
 *       pks: { models: ["PKMC18-6", "PKMC20-6-PC", ...], count: 121 }
 *     }
 *   }
 *
 * Model extraction is title-based with a regex that matches common model
 * patterns: 2-4 letter prefix + digits + optional suffix. Filtering removes
 * common false positives (year suffixes, TSB numbers, etc).
 */

export const maxDuration = 30;
export const runtime = "nodejs";

// Common patterns that look like model numbers but aren't
const NOT_A_MODEL = /^(TSB|REV|IOM|FR|SD|EN|US|YR|CES|ANSI|ALI)$/i;

function extractModels(titles: string[]): string[] {
  const found = new Set<string>();
  for (const title of titles) {
    if (!title) continue;
    // Match patterns like: VLE10, LE12-3S, PKMC18-6, HD-14LSX, XPR-12FDL,
    // PKMC18E-8-PC, 10AP, EV4000SL, RML-1500XL, PL-6000DC
    // Heuristic: 1-6 letters + digits (optional letters/dashes after)
    const matches = title.match(/\b[A-Z][A-Z0-9]{0,5}-?\d{1,5}[A-Z0-9\-]{0,10}\b/g) || [];
    for (const m of matches) {
      const clean = m.trim().toUpperCase();
      // Filter false positives
      if (clean.length < 3) continue;
      if (clean.length > 25) continue;
      if (NOT_A_MODEL.test(clean)) continue;
      // Skip pure numbers
      if (/^\d+$/.test(clean)) continue;
      // Skip year-like 4-digit (2024, 2025)
      if (/^20\d{2}$/.test(clean)) continue;
      // Skip TSB numbers (TSB_169, TSB-129)
      if (/^TSB[\-_]?\d/i.test(clean)) continue;
      // Must contain at least one digit
      if (!/\d/.test(clean)) continue;
      // Must have at least one letter
      if (!/[A-Z]/.test(clean)) continue;
      found.add(clean);
    }
  }
  return Array.from(found).sort();
}

export async function GET(req: NextRequest) {
  try {
    const brands = (req.nextUrl.searchParams.get("brands") || "bendpak,challenger,pks")
      .split(",")
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);

    if (brands.length === 0) {
      return NextResponse.json({ ok: false, error: "brands param required" }, { status: 400 });
    }

    const sql = getSQL();
    const result: Record<string, { models: string[]; count: number }> = {};

    for (const brandName of brands) {
      const rows = (await sql`
        SELECT ki.title
        FROM knowledge_items ki
        JOIN brands b ON b.id = ki.brand_id
        WHERE b.name = ${brandName} AND b.we_carry = TRUE
        ORDER BY ki.created_at DESC
        LIMIT 500
      `) as Array<{ title: string }>;

      const titles = rows.map((r) => r.title || "");
      const models = extractModels(titles);
      result[brandName] = { models, count: titles.length };
    }

    return NextResponse.json({ ok: true, brands: result });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: String(e?.message || e).slice(0, 500) },
      { status: 500 }
    );
  }
}
