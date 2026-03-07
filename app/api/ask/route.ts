import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";
import kbData from "./knowledge-base.json";

export const maxDuration = 60;

// Build a compact text representation of the KB (no pretty-printing)
const kbText = (kbData as Record<string, unknown>[])
  .map((entry) => {
    const model = (entry.model as string) || "Unknown";
    const variant =
      (entry.variant as string) || (entry.product_type as string) || "N/A";
    return `[${model} - ${variant}] ${JSON.stringify(entry)}`;
  })
  .join("\n");

const SYSTEM_PROMPT = `You are a Mohawk Lifts product expert. You have access to a comprehensive knowledge base of Mohawk Lifts products below. This includes parallelogram lifts (surface mount and flush mount), rolling jacks, installation drawings with dimensions, slab requirements, and truck data forms.

KNOWLEDGE BASE:
${kbText}

INSTRUCTIONS:
- Answer questions accurately using ONLY the knowledge base above.
- Be precise with numbers, units, and model numbers.
- If comparing models, organize clearly with side-by-side data.
- If the KB does not contain information to answer, say so clearly. Do not fabricate data.
- Keep answers practical and useful for someone evaluating or bidding on Mohawk equipment.`;

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const question = body?.question;

    if (!question || typeof question !== "string" || !question.trim()) {
      return NextResponse.json(
        { error: "Please enter a question." },
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
    if (
      typeof err === "object" &&
      err !== null &&
      "status" in err &&
      (err as { status: number }).status === 401
    ) {
      message = "API key not configured. Add ANTHROPIC_API_KEY in Vercel settings.";
    }
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
