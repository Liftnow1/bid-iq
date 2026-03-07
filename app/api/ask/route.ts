import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";
import kbData from "./knowledge-base.json";

export const maxDuration = 60;

// Build a compact text representation of the KB
const kbEntries = (kbData as Record<string, unknown>[]).map((entry) => {
  const model = (entry.model as string) || "Unknown";
  const variant =
    (entry.variant as string) || (entry.product_type as string) || "N/A";
  return `[${model} - ${variant}] ${JSON.stringify(entry)}`;
});
const kbText = kbEntries.join("\n");

const SYSTEM_PROMPT = `You are a Mohawk Lifts product expert assistant. You answer questions about Mohawk Lifts products using the knowledge base provided below.

<knowledge_base>
${kbText}
</knowledge_base>

Rules:
- Answer based ONLY on the knowledge base above
- Be precise with numbers, units, and model numbers
- If comparing models, organize clearly
- If the knowledge base does not contain the answer, say "I don't have information about that in the Mohawk Lifts knowledge base" and explain what data IS available
- Never say "please enter a question" or anything similar — always attempt to answer`;

export async function POST(request: NextRequest) {
  try {
    const text = await request.text();
    let question = "";

    try {
      const body = JSON.parse(text);
      question = body?.question || "";
    } catch {
      return NextResponse.json(
        { error: `Invalid request body: ${text.slice(0, 200)}` },
        { status: 400 }
      );
    }

    if (!question || typeof question !== "string" || !question.trim()) {
      return NextResponse.json(
        { error: `No question found in body. Received keys: ${text.slice(0, 200)}` },
        { status: 400 }
      );
    }

    const client = new Anthropic();

    const response = await client.messages.create({
      model: "claude-sonnet-4-20250514",
      max_tokens: 4096,
      system: SYSTEM_PROMPT,
      messages: [
        {
          role: "user",
          content: question.trim(),
        },
      ],
    });

    const textBlock = response.content.find((block) => block.type === "text");
    if (!textBlock || textBlock.type !== "text") {
      return NextResponse.json(
        { error: "No response from AI" },
        { status: 500 }
      );
    }

    return NextResponse.json({ answer: textBlock.text });
  } catch (err: unknown) {
    console.error("Ask error:", err);
    let message = "Failed to answer question";
    if (err instanceof Error) {
      message = err.message;
    }
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

// Test endpoint to verify the route is working
export async function GET() {
  return NextResponse.json({
    status: "ok",
    kb_entries: kbEntries.length,
    kb_size_chars: kbText.length,
    has_api_key: !!process.env.ANTHROPIC_API_KEY,
  });
}
