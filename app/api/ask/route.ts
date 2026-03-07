import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";
import kbData from "./knowledge-base.json";

export const maxDuration = 60;

const kbText = (kbData as Record<string, unknown>[])
  .map((entry) => {
    const model = (entry.model as string) || "Unknown";
    const variant =
      (entry.variant as string) || (entry.product_type as string) || "N/A";
    return `=== ${model} (${variant}) ===\n${JSON.stringify(entry, null, 2)}`;
  })
  .join("\n\n");

const SYSTEM_PROMPT = `You are a Mohawk Lifts product expert. You have access to a comprehensive knowledge base of Mohawk Lifts products including parallelogram lifts, rolling jacks, installation drawings, slab requirements, and truck data.

Answer questions accurately based ONLY on the knowledge base data provided. When citing specifications, be precise with numbers and units. If comparing models, organize the comparison clearly.

If the knowledge base does not contain information to answer the question, say so clearly — do not make up data.

When relevant, mention:
- Model numbers and variants (surface mount vs flush mount)
- Exact dimensions, capacities, and specifications
- Installation requirements (slab, electrical, pit dimensions)
- Certifications and compliance
- Parts lists and optional equipment

Keep answers clear, organized, and directly useful for someone evaluating or bidding on Mohawk Lifts equipment.`;

export async function POST(request: NextRequest) {
  try {
    const { question } = await request.json();

    if (!question || typeof question !== "string") {
      return NextResponse.json(
        { error: "Please provide a question" },
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
          content: `Here is the Mohawk Lifts knowledge base:\n\n${kbText}\n\n---\n\nQuestion: ${question}`,
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
  } catch (err) {
    console.error("Ask error:", err);
    const message =
      err instanceof Error ? err.message : "Failed to answer question";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
