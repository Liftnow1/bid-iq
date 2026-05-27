import { NextRequest, NextResponse } from "next/server";

/**
 * Reddit search proxy.
 *
 * Reddit returns 403 + HTML when called from many VPS hosting providers
 * (including the one running our self-hosted n8n at agents.liftnowdirect.com)
 * regardless of User-Agent. Vercel's IP pool is not on Reddit's blocklist,
 * so we route Reddit calls through here.
 *
 * Two modes:
 *   POST { q: string, sort?: "new"|"hot"|"top"|"relevance", t?: "hour"|"day"|"week"|"month"|"year"|"all", limit?: number }
 *   POST { queries: string[], ... }  // fetch multiple queries in one request
 *
 * Response: { ok: true, results: [{ q, data: { children: [...] } }, ...] }
 */
export const maxDuration = 60;

const REDDIT_UA = "liftnow-marketing-bot/1.0 (contact: paulj@liftnow.com)";

async function searchOne(q: string, sort: string, t: string, limit: number) {
  const url = new URL("https://www.reddit.com/search.json");
  url.searchParams.set("q", q);
  url.searchParams.set("sort", sort);
  url.searchParams.set("t", t);
  url.searchParams.set("limit", String(limit));
  url.searchParams.set("raw_json", "1");

  const res = await fetch(url.toString(), {
    headers: {
      "User-Agent": REDDIT_UA,
      Accept: "application/json",
    },
    // Vercel edge fetch — keep simple
    cache: "no-store",
  });

  const ctype = res.headers.get("content-type") || "";
  if (!res.ok || !ctype.includes("application/json")) {
    const sample = (await res.text()).slice(0, 200);
    return {
      q,
      ok: false,
      status: res.status,
      content_type: ctype,
      sample,
    };
  }
  const json = await res.json();
  return { q, ok: true, data: json.data || {} };
}

export async function POST(req: NextRequest) {
  let body: any;
  try {
    body = await req.json();
  } catch (e: any) {
    return NextResponse.json({ ok: false, error: "invalid JSON body" }, { status: 400 });
  }

  const sort = String(body.sort || "new");
  const t = String(body.t || "week");
  const limit = Math.min(Number(body.limit) || 15, 100);

  // Single query mode
  if (typeof body.q === "string") {
    const r = await searchOne(body.q, sort, t, limit);
    return NextResponse.json({ ok: true, results: [r] });
  }

  // Multi-query mode
  const queries = Array.isArray(body.queries) ? body.queries : null;
  if (!queries || queries.length === 0) {
    return NextResponse.json(
      { ok: false, error: "expected { q: string } or { queries: string[] }" },
      { status: 400 }
    );
  }

  // Fetch sequentially with small delay to avoid hitting Reddit's per-IP rate limit
  const results: any[] = [];
  for (const q of queries) {
    if (typeof q !== "string" || !q.trim()) continue;
    const r = await searchOne(q.trim(), sort, t, limit);
    results.push(r);
    // gentle delay (Reddit allows 60 req/min for anon)
    await new Promise((res) => setTimeout(res, 1100));
  }

  return NextResponse.json({ ok: true, results });
}

export async function GET() {
  return NextResponse.json({
    ok: true,
    usage: 'POST { q: string OR queries: string[], sort?, t?, limit? }',
  });
}
