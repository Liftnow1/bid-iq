import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";
import kbData from "./knowledge-base.json";

export const maxDuration = 60;

// Pre-build compact KB text - no redundant wrapping
const kbText = JSON.stringify(kbData);

const SYSTEM_PROMPT = `You are a Mohawk Lifts product expert. Answer questions using ONLY this knowledge base (JSON array of product entries):

${kbText}

Rules:
- Be precise with numbers, units, model numbers
- If comparing models, organize clearly
- If data is not in the KB, say so and list what IS available
- Always attempt to answer`;

export async function POST(request: NextRequest) {
  try {
    const text = await request.text();
    let question = "";

    try {
      const body = JSON.parse(text);
      question = body?.question || "";
    } catch {
      return NextResponse.json(
        { error: `Invalid request body` },
        { status: 400 }
      );
    }

    if (!question || typeof question !== "string" || !question.trim()) {
      return NextResponse.json(
        { error: `No question provided` },
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

export async function GET() {
  return NextResponse.json({
    status: "ok",
    kb_entries: (kbData as unknown[]).length,
    kb_size_chars: kbText.length,
    has_api_key: !!process.env.ANTHROPIC_API_KEY,
  });
}
