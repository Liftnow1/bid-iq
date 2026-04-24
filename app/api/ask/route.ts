import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";
import { getSQL, ensureSchema } from "@/lib/db";

export const maxDuration = 60;

const LIFTNOW_SYSTEM_PROMPT = `You are a product and procurement expert for Liftnow, a government-focused dealer of vehicle lifts and heavy equipment maintenance gear. Liftnow sells to fleet maintenance facilities — transit authorities, cities, counties, school districts, state agencies, military. Liftnow is a Sourcewell contract holder (121223-LFT) and holds numerous state contracts through NASPO and direct piggybacks.

When answering questions, draw on Liftnow's knowledge base of product specs, pricing, bid history, compliance data, and competitive intelligence. Be factual and concise. If the knowledge base doesn't contain enough information to answer confidently, say so rather than guessing.`;

type KnowledgeRow = {
  id: number;
  title: string;
  category: string;
  summary: string | null;
  tags: string[] | null;
  raw_content: string;
  source: string | null;
  source_filename: string | null;
};

async function searchKnowledge(question: string): Promise<KnowledgeRow[]> {
  await ensureSchema();
  const sql = getSQL();

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
    const rows = (await sql`
      SELECT id, title, category, summary, tags, raw_content, source, source_filename,
             ts_rank(
               to_tsvector('english', coalesce(title,'') || ' ' || coalesce(summary,'') || ' ' || coalesce(raw_content,'')),
               to_tsquery('english', ${tsQuery})
             ) AS rank
      FROM knowledge_items
      WHERE to_tsvector('english', coalesce(title,'') || ' ' || coalesce(summary,'') || ' ' || coalesce(raw_content,''))
            @@ to_tsquery('english', ${tsQuery})
      ORDER BY rank DESC
      LIMIT 25
    `) as unknown as KnowledgeRow[];
    add(rows);
  }

  for (const pattern of modelPatterns.slice(0, 6)) {
    const like = `%${pattern.toLowerCase().replace(/\s+/g, "%")}%`;
    const rows = (await sql`
      SELECT id, title, category, summary, tags, raw_content, source, source_filename
      FROM knowledge_items
      WHERE EXISTS (
              SELECT 1 FROM unnest(tags) t WHERE lower(t) LIKE ${like}
            )
         OR lower(title) LIKE ${like}
      LIMIT 10
    `) as unknown as KnowledgeRow[];
    add(rows);
  }

  if (collected.size === 0) {
    for (const word of words.filter((w) => w.length > 2).slice(0, 3)) {
      const like = `%${word}%`;
      const rows = (await sql`
        SELECT id, title, category, summary, tags, raw_content, source, source_filename
        FROM knowledge_items
        WHERE lower(title) LIKE ${like}
           OR lower(summary) LIKE ${like}
           OR lower(category) LIKE ${like}
        LIMIT 10
      `) as unknown as KnowledgeRow[];
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

    const rows = await searchKnowledge(question.trim());

    const client = new Anthropic();
    const response = await client.messages.create({
      model: "claude-sonnet-4-20250514",
      max_tokens: 4096,
      system: LIFTNOW_SYSTEM_PROMPT,
      messages: [
        {
          role: "user",
          content: `Retrieved knowledge base entries:\n${buildContext(rows)}\n\nQuestion: ${question.trim()}`,
        },
      ],
    });

    const textBlock = response.content.find((b) => b.type === "text");
    if (!textBlock || textBlock.type !== "text") {
      return NextResponse.json({ error: "No response from AI" }, { status: 500 });
    }

    return NextResponse.json({
      answer: textBlock.text,
      sources: rows.map((r) => ({
        id: r.id,
        title: r.title,
        category: r.category,
        source_filename: r.source_filename,
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
