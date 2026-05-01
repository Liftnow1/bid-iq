import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";
import { getSQL, ensureSchema } from "@/lib/db";

export const maxDuration = 120;

const CATEGORIES = [
  "product-specifications",
  "competitive-intelligence",
  "pricing-data",
  "bid-history",
  "installation-guides",
  "manufacturer-info",
  "service-procedures",
  "compliance-certifications",
  "customer-intelligence",
  "general",
] as const;

async function classifyWithClaude(content: string, filename?: string) {
  const client = new Anthropic();

  const response = await client.messages.create({
    model: "claude-sonnet-4-20250514",
    max_tokens: 2048,
    system: `You are a knowledge base classifier for a commercial/industrial equipment bid management system. Your job is to analyze incoming content and classify it for storage and future retrieval.

Classify the content into exactly ONE of these categories:
${CATEGORIES.map((c) => `- ${c}`).join("\n")}

Category descriptions:
- product-specifications: Product specs, dimensions, capacities, features, technical data sheets
- competitive-intelligence: Info about competitors, their products, pricing strategies, market positioning
- pricing-data: Price sheets, cost breakdowns, margin info, freight costs, manufacturer pricing
- bid-history: Past bid results, win/loss records, bid strategies that worked
- installation-guides: Installation procedures, requirements, site prep, setup instructions
- manufacturer-info: Manufacturer contacts, programs, partnerships, dealer info, contract details
- service-procedures: Maintenance, repair, warranty, service agreements, training procedures
- compliance-certifications: Certifications, licensing, bonding, insurance, regulatory requirements
- customer-intelligence: Customer preferences, buying patterns, grading criteria, relationship history
- general: Anything that doesn't fit neatly into the above categories

Respond with ONLY valid JSON in this exact format:
{
  "title": "Brief descriptive title (max 80 chars)",
  "category": "one-of-the-categories-above",
  "subcategory": "more specific sub-classification",
  "tags": ["tag1", "tag2", "tag3"],
  "summary": "2-3 sentence summary of the content",
  "extracted_data": {
    "key_facts": ["fact1", "fact2"],
    "entities": {"manufacturers": [], "products": [], "companies": [], "people": []},
    "numbers": {"prices": [], "capacities": [], "dimensions": []},
    "dates": [],
    "actionable_items": []
  }
}`,
    messages: [
      {
        role: "user",
        content: `Classify and extract structured data from this content${filename ? ` (from file: ${filename})` : ""}:\n\n${content.slice(0, 15000)}`,
      },
    ],
  });

  const textBlock = response.content.find((block) => block.type === "text");
  if (!textBlock || textBlock.type !== "text") {
    throw new Error("No classification response from AI");
  }

  // Parse the JSON response, handling potential markdown wrapping
  let jsonStr = textBlock.text.trim();
  const jsonMatch = jsonStr.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (jsonMatch) {
    jsonStr = jsonMatch[1].trim();
  }

  return JSON.parse(jsonStr);
}

export async function POST(request: NextRequest) {
  try {
    const contentType = request.headers.get("content-type") || "";

    let rawContent = "";
    let source = "typed";
    let sourceFilename: string | undefined;
    let contentTypeLabel = "text";

    if (contentType.includes("multipart/form-data")) {
      const formData = await request.formData();
      const file = formData.get("file") as File | null;
      const text = formData.get("text") as string | null;
      source = (formData.get("source") as string) || "uploaded";

      if (file) {
        sourceFilename = file.name;
        contentTypeLabel = file.type.includes("pdf") ? "pdf" : "document";

        // For text-based files, read as text
        if (
          file.type.includes("text") ||
          file.name.endsWith(".txt") ||
          file.name.endsWith(".csv") ||
          file.name.endsWith(".json") ||
          file.name.endsWith(".md")
        ) {
          rawContent = await file.text();
        } else if (file.type.includes("pdf")) {
          // For PDFs, we read the raw text content that can be extracted
          // In production, you'd use a PDF parsing library
          const bytes = await file.arrayBuffer();
          const textDecoder = new TextDecoder("utf-8", { fatal: false });
          const decoded = textDecoder.decode(bytes);
          // Try to extract readable text from PDF
          const textParts: string[] = [];
          const matches = decoded.matchAll(
            /\(([^)]+)\)|<([0-9A-Fa-f]+)>/g
          );
          for (const match of matches) {
            if (match[1]) textParts.push(match[1]);
          }
          rawContent =
            textParts.join(" ").trim() ||
            `[PDF file: ${file.name}, ${(file.size / 1024).toFixed(1)}KB - PDF text extraction requires server-side processing. The file metadata has been recorded.]`;
        } else {
          rawContent = await file.text();
        }
      } else if (text) {
        rawContent = text;
      }
    } else {
      const body = await request.json();
      rawContent = body.content || body.text || "";
      source = body.source || "typed";
      sourceFilename = body.filename;
    }

    if (!rawContent.trim()) {
      return NextResponse.json(
        { error: "No content provided" },
        { status: 400 }
      );
    }

    // Classify with Claude
    const classification = await classifyWithClaude(rawContent, sourceFilename);

    // Validate category. Post-multitag migration the column is TEXT[]; this
    // route's classifier still emits a single string under the old 10-tag
    // vocabulary, so wrap it in a one-element array for the INSERT below.
    // Re-classifying typed/uploaded content under the v2 3-tier vocabulary
    // is tracked separately — for now this keeps the route alive without
    // schema mismatch errors.
    const categoryStr = CATEGORIES.includes(classification.category)
      ? classification.category
      : "general";
    const category: string[] = [categoryStr];

    // Build search text. Use the full raw_content — Postgres FTS handles
    // multi-MB tsvectors fine, and truncating to 5000 chars caused long
    // install manuals to only have ~6% of their body indexed (queries
    // about anchor patterns, concrete depth, etc. couldn't find them).
    const searchText = [
      classification.title,
      classification.summary,
      classification.subcategory,
      ...(classification.tags || []),
      rawContent,
    ]
      .filter(Boolean)
      .join(" ");

    // Store in database
    await ensureSchema();
    const sql = getSQL();

    const result = await sql`
      INSERT INTO knowledge_items (
        title, category, subcategory, tags, content_type, source,
        source_filename, raw_content, extracted_data, summary, search_text
      ) VALUES (
        ${classification.title},
        ${category},
        ${classification.subcategory || null},
        ${classification.tags || []},
        ${contentTypeLabel},
        ${source},
        ${sourceFilename || null},
        ${rawContent},
        ${JSON.stringify(classification.extracted_data || {})},
        ${classification.summary},
        ${searchText}
      )
      RETURNING id, title, category, subcategory, tags, summary, created_at
    `;

    return NextResponse.json({
      success: true,
      item: result[0],
      classification: {
        category,
        subcategory: classification.subcategory,
        tags: classification.tags,
        summary: classification.summary,
      },
    });
  } catch (err) {
    console.error("Knowledge base ingest error:", err);
    const message = err instanceof Error ? err.message : "Failed to ingest content";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
