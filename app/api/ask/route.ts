import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";
import { getSQL, ensureSchema } from "@/lib/db";
import {
  runPythonUpgrade,
  isTierTwo,
  isUpgradeAvailable,
  tierOf,
} from "@/lib/upgrade";

// Auto-upgrade can take 30s-2min per Tier-1 source. Bump max duration so
// the first query against a fresh Tier-1 row doesn't time out.
export const maxDuration = 300;

const LIFTNOW_SYSTEM_PROMPT = `You are a product and procurement expert for Liftnow, a government-focused dealer of vehicle lifts and heavy equipment maintenance gear. Liftnow sells to fleet maintenance facilities — transit authorities, cities, counties, school districts, state agencies, military. Liftnow is a Sourcewell contract holder (121223-LFT) and holds numerous state contracts through NASPO and direct piggybacks.

When answering questions, draw on Liftnow's knowledge base of product specs, pricing, bid history, compliance data, and competitive intelligence. Be factual and concise. If the knowledge base doesn't contain enough information to answer confidently, say so rather than guessing.

The sources below are numbered [1], [2], … When you use a fact from a source, cite it inline using its number, e.g. "the CL10A has a 10,000 lb capacity [3]." Cite every claim that depends on a source. Do not invent citations or cite sources you didn't actually use. If the available sources don't contain enough information to answer confidently, say so rather than guessing.`;

type QueryMode = "cert-inclusive" | "commercial-only";

type KnowledgeRow = {
  id: number;
  title: string;
  // TEXT[] of v2 3-tier access-model values (see docs/classifier-system-prompt-v2.md).
  category: string[];
  summary: string | null;
  tags: string[] | null;
  raw_content: string | null;
  source: string | null;
  source_filename: string | null;
  source_path: string | null;
  extractor_version: string | null;
  extracted_data: Record<string, unknown> | null;
  brand_id: number | null;
  brand_name: string | null;
  created_at: string;
};

// Top N rows by retrieval rank that are eligible for auto-upgrade. If a
// Tier-1 row makes it into this slice, we pay the upgrade cost before
// answering.
const AUTO_UPGRADE_TOP_N = 5;

// Multiplicative boost applied to ts_rank for rows whose source_type is
// 'ingested_pdf'. Real PDF extractions outrank ALI cert metadata when both
// match the query. Tune via SOURCE_TYPE_PDF_BOOST env var (positive number).
const SOURCE_TYPE_PDF_BOOST = (() => {
  const raw = process.env.SOURCE_TYPE_PDF_BOOST;
  if (!raw) return 1.5;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : 1.5;
})();

function isCertQuery(question: string): boolean {
  const q = question.toLowerCase();
  return /\b(ali|certified|certification|cert\s+number|cert\s+date)\b/.test(q);
}

async function searchKnowledge(
  question: string,
  mode: QueryMode
): Promise<KnowledgeRow[]> {
  await ensureSchema();
  const sql = getSQL();
  const commercialOnly = mode === "commercial-only";

  const words = question
    .toLowerCase()
    .replace(/[^a-z0-9\s\-\/]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 1);

  const tsQuery = words
    .filter((w) => w.length > 2)
    .slice(0, 12)
    .join(" | ");

  const modelPatterns =
    question.match(/\b[A-Za-z]{1,4}[\s-]?\d{1,3}[A-Za-z]?(?:[\s-]\d{1,3}[A-Za-z]*)?\b/g) || [];

  const collected = new Map<number, KnowledgeRow>();
  const add = (rows: KnowledgeRow[]) => {
    for (const r of rows) if (!collected.has(r.id)) collected.set(r.id, r);
  };

  if (tsQuery) {
    const rows = commercialOnly
      ? ((await sql`
          SELECT ki.id, ki.title, ki.category, ki.summary, ki.tags, ki.raw_content,
                 ki.source, ki.source_filename, ki.source_path, ki.extractor_version,
                 ki.extracted_data,
                 ki.brand_id, b.name AS brand_name, ki.created_at,
                 ts_rank(
                   to_tsvector('english', coalesce(ki.title,'') || ' ' || coalesce(ki.summary,'') || ' ' || coalesce(ki.raw_content,'')),
                   to_tsquery('english', ${tsQuery})
                 ) * CASE WHEN ki.source_type = 'ingested_pdf' THEN ${SOURCE_TYPE_PDF_BOOST}::float ELSE 1.0 END AS rank_score
          FROM knowledge_items ki
          LEFT JOIN brands b ON b.id = ki.brand_id
          WHERE to_tsvector('english', coalesce(ki.title,'') || ' ' || coalesce(ki.summary,'') || ' ' || coalesce(ki.raw_content,''))
                @@ to_tsquery('english', ${tsQuery})
            AND (ki.extractor_version IS NULL OR ki.extractor_version != 'catalog-db-migration')
          ORDER BY rank_score DESC
          LIMIT 25
        `) as unknown as KnowledgeRow[])
      : ((await sql`
          SELECT ki.id, ki.title, ki.category, ki.summary, ki.tags, ki.raw_content,
                 ki.source, ki.source_filename, ki.source_path, ki.extractor_version,
                 ki.extracted_data,
                 ki.brand_id, b.name AS brand_name, ki.created_at,
                 ts_rank(
                   to_tsvector('english', coalesce(ki.title,'') || ' ' || coalesce(ki.summary,'') || ' ' || coalesce(ki.raw_content,'')),
                   to_tsquery('english', ${tsQuery})
                 ) * CASE WHEN ki.source_type = 'ingested_pdf' THEN ${SOURCE_TYPE_PDF_BOOST}::float ELSE 1.0 END AS rank_score
          FROM knowledge_items ki
          LEFT JOIN brands b ON b.id = ki.brand_id
          WHERE to_tsvector('english', coalesce(ki.title,'') || ' ' || coalesce(ki.summary,'') || ' ' || coalesce(ki.raw_content,''))
                @@ to_tsquery('english', ${tsQuery})
          ORDER BY rank_score DESC
          LIMIT 25
        `) as unknown as KnowledgeRow[]);
    add(rows);
  }

  for (const pattern of modelPatterns.slice(0, 6)) {
    const like = `%${pattern.toLowerCase().replace(/\s+/g, "%")}%`;
    const rows = commercialOnly
      ? ((await sql`
          SELECT ki.id, ki.title, ki.category, ki.summary, ki.tags, ki.raw_content,
                 ki.source, ki.source_filename, ki.source_path, ki.extractor_version,
                 ki.extracted_data,
                 ki.brand_id, b.name AS brand_name, ki.created_at
          FROM knowledge_items ki
          LEFT JOIN brands b ON b.id = ki.brand_id
          WHERE (EXISTS (
                   SELECT 1 FROM unnest(ki.tags) t WHERE lower(t) LIKE ${like}
                 )
                 OR lower(ki.title) LIKE ${like})
            AND (ki.extractor_version IS NULL OR ki.extractor_version != 'catalog-db-migration')
          LIMIT 10
        `) as unknown as KnowledgeRow[])
      : ((await sql`
          SELECT ki.id, ki.title, ki.category, ki.summary, ki.tags, ki.raw_content,
                 ki.source, ki.source_filename, ki.source_path, ki.extractor_version,
                 ki.extracted_data,
                 ki.brand_id, b.name AS brand_name, ki.created_at
          FROM knowledge_items ki
          LEFT JOIN brands b ON b.id = ki.brand_id
          WHERE EXISTS (
                  SELECT 1 FROM unnest(ki.tags) t WHERE lower(t) LIKE ${like}
                )
             OR lower(ki.title) LIKE ${like}
          LIMIT 10
        `) as unknown as KnowledgeRow[]);
    add(rows);
  }

  if (collected.size === 0) {
    for (const word of words.filter((w) => w.length > 2).slice(0, 3)) {
      const like = `%${word}%`;
      const rows = commercialOnly
        ? ((await sql`
            SELECT ki.id, ki.title, ki.category, ki.summary, ki.tags, ki.raw_content,
                   ki.source, ki.source_filename, ki.source_path, ki.extractor_version,
                   ki.extracted_data,
                   ki.brand_id, b.name AS brand_name, ki.created_at
            FROM knowledge_items ki
            LEFT JOIN brands b ON b.id = ki.brand_id
            WHERE (lower(ki.title) LIKE ${like}
                   OR lower(ki.summary) LIKE ${like}
                   OR EXISTS (SELECT 1 FROM unnest(ki.category) c WHERE lower(c) LIKE ${like}))
              AND (ki.extractor_version IS NULL OR ki.extractor_version != 'catalog-db-migration')
            LIMIT 10
          `) as unknown as KnowledgeRow[])
        : ((await sql`
            SELECT ki.id, ki.title, ki.category, ki.summary, ki.tags, ki.raw_content,
                   ki.source, ki.source_filename, ki.source_path, ki.extractor_version,
                   ki.extracted_data,
                   ki.brand_id, b.name AS brand_name, ki.created_at
            FROM knowledge_items ki
            LEFT JOIN brands b ON b.id = ki.brand_id
            WHERE lower(ki.title) LIKE ${like}
               OR lower(ki.summary) LIKE ${like}
               OR EXISTS (SELECT 1 FROM unnest(ki.category) c WHERE lower(c) LIKE ${like})
            LIMIT 10
          `) as unknown as KnowledgeRow[]);
      add(rows);
    }
  }

  return Array.from(collected.values()).slice(0, 25);
}

// Per-candidate body sent to the answering model.
//   - The top FULL_CONTENT_TOP_N candidates by rank get their FULL raw_content
//     (no truncation). Long install manuals — anchor patterns, concrete depth,
//     electrical specs — live mid-document past any small slice.
//   - Lower-ranked candidates are truncated to TRUNCATED_BODY_CHARS to cap
//     cost on borderline hits.
//   - The total payload (heads + summaries + bodies + separators) is capped at
//     TOTAL_PAYLOAD_CAP_CHARS by dropping the lowest-ranked candidate first.
const FULL_CONTENT_TOP_N = (() => {
  const raw = process.env.FULL_CONTENT_TOP_N;
  if (!raw) return 5;
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : 5;
})();
const TRUNCATED_BODY_CHARS = 5_000;
const TOTAL_PAYLOAD_CAP_CHARS = 200_000;
const CONTEXT_SEPARATOR = "\n\n---\n\n";

function buildContext(rows: KnowledgeRow[]): string {
  if (rows.length === 0) return "No relevant entries found in the knowledge base.";

  const candidates = rows.map((r, i) => {
    const cats = Array.isArray(r.category) ? r.category.join(",") : String(r.category ?? "");
    const head = `[${i + 1}] ${r.title}  (category=${cats}${
      r.source_filename ? `, file=${r.source_filename}` : ""
    })`;
    const summary = r.summary ? `Summary: ${r.summary}` : "";
    const fullBody = r.raw_content ? String(r.raw_content) : "";
    const body =
      i < FULL_CONTENT_TOP_N ? fullBody : fullBody.slice(0, TRUNCATED_BODY_CHARS);
    const text = [head, summary, body].filter(Boolean).join("\n");
    return { text, length: text.length };
  });

  // Drop the lowest-ranked candidate(s) until we're under the global cap.
  // Always keep at least one candidate even if it exceeds the cap on its own —
  // a truncated long top hit is still better than an empty context.
  let total = candidates.reduce(
    (acc, c, idx) => acc + c.length + (idx > 0 ? CONTEXT_SEPARATOR.length : 0),
    0
  );
  while (total > TOTAL_PAYLOAD_CAP_CHARS && candidates.length > 1) {
    const dropped = candidates.pop()!;
    total -= dropped.length + CONTEXT_SEPARATOR.length;
  }

  return candidates.map((c) => c.text).join(CONTEXT_SEPARATOR);
}

/**
 * Pull every [N] citation marker out of the model's answer. Returns the
 * unique 1-based source indices it referenced.
 */
function parseCitedIndices(answer: string): Set<number> {
  const cited = new Set<number>();
  const re = /\[(\d+)\]/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(answer)) !== null) {
    const n = Number(m[1]);
    if (Number.isInteger(n) && n > 0) cited.add(n);
  }
  return cited;
}

type UpgradeOutcome = {
  upgraded: Set<number>;
  skippedDueToUnavailable: number[];
};

/**
 * Identify Tier-1 rows in the top-N retrieval slice. If the runtime
 * supports the Python upgrade flow, upgrade them in parallel. Otherwise
 * leave them as Tier-1 and let the caller answer from metadata only —
 * never throw, never block the response.
 */
async function upgradeTier1Candidates(rows: KnowledgeRow[]): Promise<UpgradeOutcome> {
  const candidates = rows
    .slice(0, AUTO_UPGRADE_TOP_N)
    .filter(
      (r) =>
        !isTierTwo({
          id: r.id,
          raw_content: r.raw_content,
          extracted_data: r.extracted_data,
        })
    );
  if (candidates.length === 0) {
    return { upgraded: new Set(), skippedDueToUnavailable: [] };
  }

  if (!(await isUpgradeAvailable())) {
    return {
      upgraded: new Set(),
      skippedDueToUnavailable: candidates.map((r) => r.id),
    };
  }

  const upgraded = new Set<number>();
  await Promise.all(
    candidates.map(async (r) => {
      const result = await runPythonUpgrade(r.id);
      if (result.ok) upgraded.add(r.id);
      else
        console.error(
          `auto-upgrade failed for id=${r.id}:`,
          result.error,
          "stderr" in result ? result.stderr : ""
        );
    })
  );
  return { upgraded, skippedDueToUnavailable: [] };
}

export async function POST(request: NextRequest) {
  try {
    const text = await request.text();
    let question = "";
    try {
      const body = JSON.parse(text);
      question = body?.question || "";
    } catch {
      return NextResponse.json({ error: "Invalid request body" }, { status: 400 });
    }
    if (!question || typeof question !== "string" || !question.trim()) {
      return NextResponse.json({ error: "No question provided" }, { status: 400 });
    }

    const trimmed = question.trim();
    const queryMode: QueryMode = isCertQuery(trimmed)
      ? "cert-inclusive"
      : "commercial-only";

    // Initial retrieval. If any Tier-1 rows score into the top slice,
    // upgrade them to Tier-2 in parallel and re-run retrieval so the now-
    // populated raw_content participates in ranking. On hosts without the
    // Python upgrade flow (e.g. Vercel serverless), the upgrade step is
    // skipped and the answer is generated from Tier-1 metadata only.
    let rows = await searchKnowledge(trimmed, queryMode);
    const outcome = await upgradeTier1Candidates(rows);
    if (outcome.upgraded.size > 0) {
      rows = await searchKnowledge(trimmed, queryMode);
    }

    const client = new Anthropic();
    const response = await client.messages.create({
      model: "claude-sonnet-4-20250514",
      max_tokens: 4096,
      system: LIFTNOW_SYSTEM_PROMPT,
      messages: [
        {
          role: "user",
          content: `Retrieved knowledge base entries:\n${buildContext(rows)}\n\nQuestion: ${trimmed}`,
        },
      ],
    });

    const textBlock = response.content.find((b) => b.type === "text");
    if (!textBlock || textBlock.type !== "text") {
      return NextResponse.json({ error: "No response from AI" }, { status: 500 });
    }

    // Filter the source-tile list down to only the [N] citations the model
    // actually used. Indices are 1-based and map into `rows` by position.
    const citedIndices = parseCitedIndices(textBlock.text);
    const citedSources = Array.from(citedIndices)
      .filter((n) => n >= 1 && n <= rows.length)
      .sort((a, b) => a - b)
      .map((n) => rows[n - 1])
      .map((r) => ({
        id: r.id,
        title: r.title,
        category: r.category,
        tier: tierOf({
          id: r.id,
          raw_content: r.raw_content,
          extracted_data: r.extracted_data,
        }),
        summary: r.summary,
        tags: r.tags,
        source_filename: r.source_filename,
        source_path: r.source_path,
        extractor_version: r.extractor_version,
        brand_id: r.brand_id,
        brand_name: r.brand_name,
        created_at: r.created_at,
      }));

    return NextResponse.json({
      answer: textBlock.text,
      query_mode: queryMode,
      sources: citedSources,
      upgraded_ids: Array.from(outcome.upgraded),
      tier1_unupgraded_ids: outcome.skippedDueToUnavailable,
      upgrade_available: outcome.skippedDueToUnavailable.length === 0
        ? undefined
        : false,
    });
  } catch (err: unknown) {
    console.error("Ask error:", err);
    const message = err instanceof Error ? err.message : "Failed to answer question";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function GET() {
  try {
    await ensureSchema();
    const sql = getSQL();
    // category is TEXT[] (multi-tag); unnest so the count is per-tag.
    // A 4-tag document contributes 1 to each of its 4 tag buckets.
    const counts = await sql`
      SELECT cat AS category, count(*)::int AS count
        FROM knowledge_items, unnest(category) AS cat
       GROUP BY cat
       ORDER BY count DESC
    `;
    const total = await sql`SELECT count(*)::int as total FROM knowledge_items`;
    return NextResponse.json({
      status: "ok",
      total: total[0].total,
      by_category: counts,
      has_api_key: !!process.env.ANTHROPIC_API_KEY,
      has_db: !!process.env.DATABASE_URL,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: msg, has_db: !!process.env.DATABASE_URL }, { status: 500 });
  }
}
