import { NextRequest, NextResponse } from "next/server";
import zlib from "zlib";

/**
 * Microsoft Ads Report ZIP unzipper.
 *
 * Microsoft Ads' Reporting API returns reports as ZIP files. n8n's Code-node
 * sandbox blocks `require('zlib')` and `require('https')`, which made it
 * impossible to extract these in n8n. This Vercel function is the workaround:
 * n8n submits + polls the report, gets the ReportDownloadUrl, then POSTs that
 * URL here. We fetch the ZIP, extract the CSV, parse it, and return
 * daily totals as JSON.
 *
 * Why: the DownloadUrl from MS Ads is a pre-signed Azure Blob URL with a
 * SAS token, so no auth is needed to fetch it — we just need a place that
 * can actually run zlib.
 *
 * POST body: { downloadUrl: string }
 * Response:  { ok: true, rows_parsed: number, csv_lines: number, daily: {[date]: {...}}, sample_csv_header: string }
 */
export const maxDuration = 60;

interface MsRow {
  spend: number;
  conversions: number;
  revenue: number;
  clicks: number;
  impressions: number;
}

function parseZip(buf: Buffer): string {
  // ZIP Local File Header (LFH) signature
  const SIG_LFH = 0x04034b50;
  let p = 0;
  const texts: string[] = [];
  while (p < buf.length - 30) {
    if (buf.readUInt32LE(p) !== SIG_LFH) break;
    const compMethod = buf.readUInt16LE(p + 8);
    const compSize = buf.readUInt32LE(p + 18);
    const nameLen = buf.readUInt16LE(p + 26);
    const extraLen = buf.readUInt16LE(p + 28);
    const dataStart = p + 30 + nameLen + extraLen;
    const dataEnd = dataStart + compSize;
    const compData = buf.slice(dataStart, dataEnd);
    let text = "";
    if (compMethod === 0) {
      text = compData.toString("utf-8");
    } else if (compMethod === 8) {
      try {
        text = zlib.inflateRawSync(compData).toString("utf-8");
      } catch (e) {
        text = "";
      }
    }
    texts.push(text);
    p = dataEnd;
  }
  return texts.join("\n");
}

// Normalize various date formats to YYYY-MM-DD.
// Microsoft Ads CSV reports commonly emit M/D/YYYY, MM/DD/YYYY, or ISO.
function normalizeDate(raw: string): string | null {
  if (!raw) return null;
  const trimmed = raw.trim();
  // ISO already
  if (/^\d{4}-\d{2}-\d{2}/.test(trimmed)) return trimmed.slice(0, 10);
  // US M/D/YYYY or MM/DD/YYYY
  const us = trimmed.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/);
  if (us) {
    const m = us[1].padStart(2, "0");
    const d = us[2].padStart(2, "0");
    return `${us[3]}-${m}-${d}`;
  }
  // YYYYMMDD
  const compact = trimmed.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (compact) return `${compact[1]}-${compact[2]}-${compact[3]}`;
  return null;
}

function parseCsv(csv: string): { daily: Record<string, MsRow>; rowsParsed: number; header: string; sample_dates: string[]; sample_rows: string[] } {
  const lines = csv.split(/\r?\n/).filter((l) => l.trim().length);
  if (lines.length < 2) return { daily: {}, rowsParsed: 0, header: lines[0] || "", sample_dates: [], sample_rows: [] };
  const header = lines[0].split(",").map((s) => s.replace(/^"|"$/g, "").trim());
  const sample_rows = lines.slice(1, 4);  // capture first 3 data rows for debugging
  const idx = {
    date: header.indexOf("TimePeriod"),
    spend: header.indexOf("Spend"),
    conv: header.indexOf("Conversions"),
    clicks: header.indexOf("Clicks"),
    impr: header.indexOf("Impressions"),
    revenue: header.indexOf("Revenue"),
  };
  const daily: Record<string, MsRow> = {};
  let rowsParsed = 0;
  const sample_dates: string[] = [];
  for (let i = 1; i < lines.length; i++) {
    const cols = lines[i].split(",").map((s) => s.replace(/^"|"$/g, "").trim());
    const rawDate = cols[idx.date] || "";
    if (sample_dates.length < 3) sample_dates.push(rawDate);
    const date = normalizeDate(rawDate);
    if (!date) continue;
    const spend = parseFloat((cols[idx.spend] || "0").replace(/[$,]/g, "")) || 0;
    const conv = parseFloat((cols[idx.conv] || "0").replace(/,/g, "")) || 0;
    const clicks = parseInt((cols[idx.clicks] || "0").replace(/,/g, "")) || 0;
    const impr = parseInt((cols[idx.impr] || "0").replace(/,/g, "")) || 0;
    const revenue = idx.revenue >= 0 ? parseFloat((cols[idx.revenue] || "0").replace(/[$,]/g, "")) || 0 : 0;
    if (!daily[date]) daily[date] = { spend: 0, conversions: 0, revenue: 0, clicks: 0, impressions: 0 };
    daily[date].spend += spend;
    daily[date].conversions += conv;
    daily[date].revenue += revenue;
    daily[date].clicks += clicks;
    daily[date].impressions += impr;
    rowsParsed++;
  }
  // Round for sanity
  for (const d of Object.keys(daily)) {
    daily[d].spend = Math.round(daily[d].spend * 100) / 100;
    daily[d].conversions = Math.round(daily[d].conversions * 100) / 100;
    daily[d].revenue = Math.round(daily[d].revenue * 100) / 100;
  }
  return { daily, rowsParsed, header: header.join(","), sample_dates, sample_rows };
}

export async function POST(req: NextRequest) {
  let body: any;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "Body must be JSON" }, { status: 400 });
  }
  const downloadUrl = body?.downloadUrl;
  if (!downloadUrl || typeof downloadUrl !== "string") {
    return NextResponse.json({ ok: false, error: "downloadUrl (string) required" }, { status: 400 });
  }
  if (!downloadUrl.startsWith("https://")) {
    return NextResponse.json({ ok: false, error: "downloadUrl must be https" }, { status: 400 });
  }

  let buf: Buffer;
  try {
    const res = await fetch(downloadUrl, { redirect: "follow" });
    if (!res.ok) {
      return NextResponse.json(
        { ok: false, error: `Fetch failed: HTTP ${res.status}` },
        { status: 502 },
      );
    }
    const ab = await res.arrayBuffer();
    buf = Buffer.from(ab);
  } catch (e: any) {
    return NextResponse.json({ ok: false, error: "Fetch error: " + e?.message }, { status: 502 });
  }

  if (buf.length < 22) {
    return NextResponse.json({ ok: false, error: "Downloaded payload too small to be a ZIP" }, { status: 502 });
  }

  let csv: string;
  try {
    csv = parseZip(buf);
  } catch (e: any) {
    return NextResponse.json({ ok: false, error: "Unzip error: " + e?.message }, { status: 500 });
  }
  if (!csv) {
    return NextResponse.json({ ok: false, error: "ZIP extracted but no CSV content found" }, { status: 500 });
  }

  const parsed = parseCsv(csv);
  return NextResponse.json({
    ok: true,
    bytes_downloaded: buf.length,
    csv_lines: csv.split(/\r?\n/).filter((l) => l.trim().length).length,
    rows_parsed: parsed.rowsParsed,
    sample_csv_header: parsed.header,
    sample_dates_seen: parsed.sample_dates,
    sample_rows_seen: parsed.sample_rows,
    daily: parsed.daily,
  });
}

export async function GET() {
  return NextResponse.json({
    ok: true,
    message:
      "POST { downloadUrl } — fetches a Microsoft Ads report ZIP, extracts CSV, returns parsed daily totals.",
  });
}
