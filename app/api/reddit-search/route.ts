import { NextRequest, NextResponse } from "next/server";

/**
 * Reddit search — Plan B (Brave Search API with site:reddit.com filter).
 *
 * Original plan: direct Reddit JSON API. But Reddit blocks every cloud IP
 * (n8n's Hetzner + Vercel both 403'd) for unauthenticated calls, and the
 * Reddit dev portal app-creation page silently fails for Paul.
 *
 * Plan B routes through Brave Search Web API (free tier, no Reddit account
 * needed) with `site:reddit.com` filter, returning the same shape so
 * downstream n8n parsers work unchanged.
 *
 * Env: BRAVE_SEARCH_API_KEY  (free key at https://api.search.brave.com/)
 *
 * POST { q: string, sort?, t?, limit? }  OR  { queries: string[], ... }
 * Response: { ok: true, results: [{ q, ok, data: { children: [{data:{...}}] } }] }
 *
 * If BRAVE_SEARCH_API_KEY isn't set, returns a clear error so the upstream
 * n8n HTML-guard surfaces it instead of silently failing.
 */
export const maxDuration = 60;

const BRAVE_API_KEY = process.env.BRAVE_SEARCH_API_KEY || "";

async function braveSearchReddit(q: string, limit: number) {
  if (!BRAVE_API_KEY) {
    return {
      q,
      ok: false,
      status: 500,
      content_type: "application/json",
      sample: "BRAVE_SEARCH_API_KEY not configured on Vercel — add it in Vercel env vars",
    };
  }
  const url = new URL("https://api.search.brave.com/res/v1/web/search");
  url.searchParams.set("q", `site:reddit.com ${q}`);
  url.searchParams.set("count", String(Math.min(limit, 20)));
  url.searchParams.set("safesearch", "moderate");
  url.searchParams.set("freshness", "pw"); // past week

  const res = await fetch(url.toString(), {
    headers: {
      "X-Subscription-Token": BRAVE_API_KEY,
      Accept: "application/json",
    },
    cache: "no-store",
  });

  if (!res.ok) {
    const sample = (await res.text()).slice(0, 200);
    return { q, ok: false, status: res.status, content_type: res.headers.get("content-type") || "", sample };
  }

  const json = await res.json();
  const webResults = (json.web && json.web.results) || [];

  // Reshape into Reddit-API-compatible structure so n8n parsers don't need changes.
  // Each Brave result becomes a `children` entry with data.{permalink, title, ...}.
  const children = webResults
    .filter((r: any) => r.url && r.url.includes("reddit.com/r/"))
    .map((r: any) => {
      // r.url looks like https://www.reddit.com/r/SchoolBus/comments/abc123/title-slug/
      const m = r.url.match(/reddit\.com\/r\/([^/]+)\/comments\/([^/]+)/);
      const subreddit = m ? m[1] : "";
      const permalink = r.url.replace(/^https?:\/\/(?:www\.|old\.)?reddit\.com/, "");
      return {
        kind: "t3",
        data: {
          permalink,
          subreddit,
          title: r.title || "",
          selftext: (r.description || "").slice(0, 500),
          author: "(via brave-search)",
          score: 0,
          num_comments: 0,
          created_utc: r.page_age ? Math.floor(new Date(r.page_age).getTime() / 1000) : Math.floor(Date.now() / 1000) - 86400 * 3,
          is_self: true,
          over_18: false,
          url: r.url,
        },
      };
    });

  return { q, ok: true, data: { children } };
}

export async function POST(req: NextRequest) {
  let body: any;
  try {
    body = await req.json();
  } catch (e: any) {
    return NextResponse.json({ ok: false, error: "invalid JSON body" }, { status: 400 });
  }

  const limit = Math.min(Number(body.limit) || 15, 20);

  if (typeof body.q === "string") {
    const r = await braveSearchReddit(body.q, limit);
    return NextResponse.json({ ok: true, results: [r] });
  }

  const queries = Array.isArray(body.queries) ? body.queries : null;
  if (!queries || queries.length === 0) {
    return NextResponse.json(
      { ok: false, error: "expected { q: string } or { queries: string[] }" },
      { status: 400 }
    );
  }

  // Brave free tier allows ~1 qps. Sequence with small gap.
  const results: any[] = [];
  for (const q of queries) {
    if (typeof q !== "string" || !q.trim()) continue;
    const r = await braveSearchReddit(q.trim(), limit);
    results.push(r);
    await new Promise((res) => setTimeout(res, 1100));
  }

  return NextResponse.json({ ok: true, results });
}

export async function GET() {
  return NextResponse.json({
    ok: true,
    provider: "brave-search",
    note: "POST { q: string } OR { queries: string[] }. Set BRAVE_SEARCH_API_KEY in Vercel env to enable.",
    key_configured: BRAVE_API_KEY ? true : false,
  });
}
