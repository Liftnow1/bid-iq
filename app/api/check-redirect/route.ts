import { NextRequest, NextResponse } from "next/server";

/**
 * Check Redirect — given a URL, return its redirect status without following.
 *
 * n8n Code nodes can't detect redirects: httpRequest follows them silently,
 * fetch isn't available in the sandbox, and require('https') is disallowed.
 *
 * This Vercel endpoint uses Node fetch with redirect:'manual' which works
 * properly. Bee's Build LLM Payload calls this via n8n's standard httpRequest
 * to a JSON endpoint (no redirects for n8n to chase).
 *
 * GET ?url=https://liftnow.com/types-of-vehicle-lifts/
 * Response: { ok: true, status: 301, location: "/products/vehicle-lifts/" }
 *           { ok: true, status: 200, location: null }
 *           { ok: false, error: "..." }
 */
export const maxDuration = 15;

export async function GET(req: NextRequest) {
  const url = req.nextUrl.searchParams.get("url") || "";
  if (!url) {
    return NextResponse.json({ ok: false, error: "missing url param" }, { status: 400 });
  }
  if (!/^https?:\/\//i.test(url)) {
    return NextResponse.json({ ok: false, error: "url must start with http(s)://" }, { status: 400 });
  }
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    const res = await fetch(url, {
      method: "HEAD",
      redirect: "manual",
      signal: controller.signal,
    });
    clearTimeout(timeout);
    const location = res.headers.get("location");
    return NextResponse.json({
      ok: true,
      status: res.status,
      location,
      redirected: res.status >= 300 && res.status < 400 && !!location,
    });
  } catch (e: any) {
    return NextResponse.json({
      ok: false,
      error: String(e?.message || e).slice(0, 200),
    }, { status: 200 });
  }
}
