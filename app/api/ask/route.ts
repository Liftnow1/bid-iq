import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";
import { getSQL } from "@/lib/db";

export const maxDuration = 60;

async function searchProducts(question: string) {
  const sql = getSQL();

  // Extract potential model numbers and keywords from the question
  const words = question
    .toLowerCase()
    .replace(/[^a-z0-9\s\-\/]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 1);

  // 1. Try exact model match first
  const modelPatterns = question.match(
    /\b[A-Za-z]{1,4}[\s-]?\d{1,3}[A-Za-z]?(?:[\s-]\d{1,3}[A-Za-z]*)?\b/g
  );

  let results: Record<string, unknown>[] = [];

  if (modelPatterns && modelPatterns.length > 0) {
    // Search by model patterns
    for (const pattern of modelPatterns.slice(0, 5)) {
      const modelResults = await sql`
        SELECT DISTINCT ON (model, variant) data
        FROM products
        WHERE model ILIKE ${"%" + pattern + "%"}
           OR variant ILIKE ${"%" + pattern + "%"}
           OR search_text ILIKE ${"%" + pattern + "%"}
        ORDER BY model, variant,
          CASE WHEN document_type IN ('spec-sheet', 'data-sheet') THEN 0 ELSE 1 END
        LIMIT 10
      `;
      results.push(...modelResults.map((r) => r.data as Record<string, unknown>));
    }
  }

  // 2. Full-text search for broader queries
  const tsQuery = words
    .filter((w) => w.length > 2)
    .slice(0, 8)
    .join(" | ");

  if (tsQuery) {
    const textResults = await sql`
      SELECT data, ts_rank(to_tsvector('english', search_text), to_tsquery('english', ${tsQuery})) as rank
      FROM products
      WHERE to_tsvector('english', search_text) @@ to_tsquery('english', ${tsQuery})
      ORDER BY rank DESC
      LIMIT 15
    `;
    results.push(...textResults.map((r) => r.data as Record<string, unknown>));
  }

  // 3. If still no results, try ILIKE on key fields
  if (results.length === 0) {
    for (const word of words.filter((w) => w.length > 2).slice(0, 3)) {
      const likeResults = await sql`
        SELECT data FROM products
        WHERE model ILIKE ${"%" + word + "%"}
           OR product_type ILIKE ${"%" + word + "%"}
           OR category ILIKE ${"%" + word + "%"}
           OR capacity ILIKE ${"%" + word + "%"}
        LIMIT 10
      `;
      results.push(...likeResults.map((r) => r.data as Record<string, unknown>));
    }
  }

  // Deduplicate by source_file
  const seen = new Set<string>();
  const unique: Record<string, unknown>[] = [];
  for (const r of results) {
    const key = (r.source_file as string) || JSON.stringify(r).slice(0, 100);
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(r);
  }

  return unique.slice(0, 25); // Cap at 25 entries for context
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

    // Search for relevant products
    const relevant = await searchProducts(question.trim());

    const contextText =
      relevant.length > 0
        ? JSON.stringify(relevant)
        : "No matching products found in the database.";

    const client = new Anthropic();

    const response = await client.messages.create({
      model: "claude-sonnet-4-20250514",
      max_tokens: 4096,
      system: `You are a Mohawk Lifts product expert. Answer questions using the product data retrieved from the database below. Be precise with numbers, units, and model numbers. If comparing models, organize clearly. If the data doesn't contain the answer, say so.`,
      messages: [
        {
          role: "user",
          content: `Retrieved product data:\n${contextText}\n\nQuestion: ${question.trim()}`,
        },
      ],
    });

    const textBlock = response.content.find((block) => block.type === "text");
    if (!textBlock || textBlock.type !== "text") {
      return NextResponse.json({ error: "No response from AI" }, { status: 500 });
    }

    return NextResponse.json({
      answer: textBlock.text,
      sources: relevant.length,
    });
  } catch (err: unknown) {
    console.error("Ask error:", err);
    let message = "Failed to answer question";
    if (err instanceof Error) message = err.message;
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function GET() {
  try {
    const sql = getSQL();
    const result = await sql`
      SELECT
        count(*) as total,
        count(DISTINCT model) as models,
        count(DISTINCT category) as categories,
        count(DISTINCT manufacturer) as manufacturers
      FROM products
    `;
    return NextResponse.json({
      status: "ok",
      ...result[0],
      has_api_key: !!process.env.ANTHROPIC_API_KEY,
      has_db: !!process.env.DATABASE_URL,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: msg, has_db: !!process.env.DATABASE_URL }, { status: 500 });
  }
}
