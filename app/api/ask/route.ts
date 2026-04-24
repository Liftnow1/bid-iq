import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";
import { getSQL, ensureSchema } from "@/lib/db";

export const maxDuration = 60;

const LIFTNOW_SYSTEM_PROMPT = `You are a product and procurement expert for Liftnow, a government-focused dealer of vehicle lifts and heavy equipment maintenance gear. Liftnow sells to fleet maintenance facilities — transit authorities, cities, counties, school districts, state agencies, military. Liftnow is a Sourcewell contract holder (121223-LFT) and holds numerous state contracts through NASPO and direct piggybacks.

When answering questions, draw on Liftnow's knowledge base of product specs, pricing, bid history, compliance data, and competitive intelligence. Be factual and concise. If the knowledge base doesn't contain enough information to answer confidently, say so rather than guessing.

The sources below represent Liftnow's current commercial catalog and extracted product documentation. When specific ALI certification records are relevant, they may also be included. If the available sources don't contain enough information to answer confidently, say so rather than guessing.`;

type QueryMode = "cert-inclusive" | "commercial-only";

type KnowledgeRow = {
  id: number;
  title: string;
  category: string;
  summary: string | null;
  tags: string[] | null;
  raw_content: string;
  source: string | null;
  source_filename: string | null;
  source_path: string | null;
  extractor_version: string | null;
  brand_id: number | null;
  brand_name: string | null;
  created_at: string;
};

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
                 ki.brand_id, b.name AS brand_name, ki.created_at,
                 ts_rank(
                   to_tsvector('english', coalesce(ki.title,'') || ' ' || coalesce(ki.summary,'') || ' ' || coalesce(ki.raw_content,'')),
                   to_tsquery('english', ${tsQuery})
                 ) AS rank
          FROM knowledge_items ki
          LEFT JOIN brands b ON b.id = ki.brand_id
          WHERE to_tsvector('english', coalesce(ki.title,'') || ' ' || coalesce(ki.summary,'') || ' ' || coalesce(ki.raw_content,''))
                @@ to_tsquery('english', ${tsQuery})
            AND (ki.extractor_version IS NULL OR ki.extractor_version != 'catalog-db-migration')
          ORDER BY rank DESC
          LIMIT 25
        `) as unknown as KnowledgeRow[])
      : ((await sql`
          SELECT ki.id, ki.title, ki.category, ki.summary, ki.tags, ki.raw_content,
                 ki.source, ki.source_filename, ki.source_path, ki.extractor_version,
                 ki.brand_id, b.name AS brand_name, ki.created_at,
                 ts_rank(
                   to_tsvector('english', coalesce(ki.title,'') || ' ' || coalesce(ki.summary,'') || ' ' || coalesce(ki.raw_content,'')),
                   to_tsquery('english', ${tsQuery})
                 ) AS rank
          FROM knowledge_items ki
          LEFT JOIN brands b ON b.id = ki.brand_id
          WHERE to_tsvector('english', coalesce(ki.title,'') || ' ' || coalesce(ki.summary,'') || ' ' || coalesce(ki.raw_content,''))
                @@ to_tsquery('english', ${tsQuery})
          ORDER BY rank DESC
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
                   ki.brand_id, b.name AS brand_name, ki.created_at
            FROM knowledge_items ki
            LEFT JOIN brands b ON b.id = ki.brand_id
            WHERE (lower(ki.title) LIKE ${like}
                   OR lower(ki.summary) LIKE ${like}
                   OR lower(ki.category) LIKE ${like})
              AND (ki.extractor_version IS NULL OR ki.extractor_version != 'catalog-db-migration')
            LIMIT 10
          `) as unknown as KnowledgeRow[])
        : ((await sql`
            SELECT ki.id, ki.title, ki.category, ki.summary, ki.tags, ki.raw_content,
                   ki.source, ki.source_filename, ki.source_path, ki.extractor_version,
                   ki.brand_id, b.name AS brand_name, ki.created_at
            FROM knowledge_items ki
            LEFT JOIN brands b ON b.id = ki.brand_id
            WHERE lower(ki.title) LIKE ${like}
               OR lower(ki.summary) LIKE ${like}
               OR lower(ki.category) LIKE ${like}
            LIMIT 10
          `) as unknown as KnowledgeRow[]);
      add(rows);
    }
  }

  return Array.from(collected.values()).slice(0, 25);
}

function buildContext(rows: KnowledgeRow[]): string {
  if (rows.length === 0) return "No relevant entries found in the knowledge base.";
  return rows
    .map((r, i) => {
      const head = `[${i + 1}] ${r.title}  (category=${r.category}${r.source_filename ? `, file=${r.source_filename}` : ""})`;
      const body = r.raw_content ? String(r.raw_content).slice(0, 2500) : "";
      const summary = r.summary ? `Summary: ${r.summary}` : "";
      return [head, summary, body].filter(Boolean).join("\n");
    })
    .join("\n\n---\n\n");
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
    const rows = await searchKnowledge(trimmed, queryMode);

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

    return NextResponse.json({
      answer: textBlock.text,
      query_mode: queryMode,
      sources: rows.map((r) => ({
        id: r.id,
        title: r.title,
        category: r.category,
        summary: r.summary,
        tags: r.tags,
        source_filename: r.source_filename,
        source_path: r.source_path,
        extractor_version: r.extractor_version,
        brand_id: r.brand_id,
        brand_name: r.brand_name,
        created_at: r.created_at,
      })),
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
    const counts = await sql`
      SELECT category, count(*)::int as count
      FROM knowledge_items
      GROUP BY category
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
