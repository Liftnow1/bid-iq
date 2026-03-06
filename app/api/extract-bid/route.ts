import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";

export const maxDuration = 60;

const EXTRACTION_PROMPT = `You are a bid analysis expert. Analyze this bid document and extract structured data.

Return a JSON object with exactly these fields:
{
  "title": "bid title/project name",
  "issuing_agency": "agency or organization issuing the bid",
  "bid_number": "bid/RFP/IFB number",
  "bid_deadline": "submission deadline date and time",
  "qa_deadline": "Q&A or questions deadline",
  "site_visit": "site visit details and whether mandatory",
  "delivery_method": "how the bid must be delivered (electronic, mail, in-person)",
  "key_dates": [{"date": "date", "description": "what happens"}],
  "scope_summary": "detailed summary of what is being requested",
  "products_requested": ["specific products, brand names, or part numbers mentioned"],
  "specifications": ["key technical specifications or requirements"],
  "substitutions_allowed": true/false,
  "substitution_details": "details about substitution/alternate/equivalent policies",
  "required_forms": ["forms that must be submitted with the bid"],
  "bonds_required": ["bid bond, performance bond, etc. with amounts if specified"],
  "insurance_requirements": "insurance requirements",
  "licensing_requirements": "licensing or certification requirements",
  "evaluation_criteria": ["how bids will be evaluated/scored"],
  "services_included": ["services included in scope"],
  "services_excluded": ["services explicitly excluded"],
  "strategic_notes": "strategic observations - competitive advantages, specification gaps, potential defenses, concerns about spec compliance, substitution strategy",
  "risk_assessment": "risks identified - ambiguous scope, tight timeline, bonding requirements, geographic challenges, specification deficiencies"
}

Be thorough. If information is not found in the document, use "Not specified" for strings and empty arrays for lists.
Return ONLY the JSON object, no markdown fences or extra text.`;

export async function POST(request: NextRequest) {
  try {
    const formData = await request.formData();
    const file = formData.get("file") as File;

    if (!file) {
      return NextResponse.json({ error: "No file provided" }, { status: 400 });
    }

    if (!file.type.includes("pdf")) {
      return NextResponse.json(
        { error: "File must be a PDF" },
        { status: 400 }
      );
    }

    const bytes = await file.arrayBuffer();
    const base64 = Buffer.from(bytes).toString("base64");

    const client = new Anthropic();

    const response = await client.messages.create({
      model: "claude-sonnet-4-20250514",
      max_tokens: 8192,
      messages: [
        {
          role: "user",
          content: [
            {
              type: "document",
              source: {
                type: "base64",
                media_type: "application/pdf",
                data: base64,
              },
            },
            {
              type: "text",
              text: EXTRACTION_PROMPT,
            },
          ],
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

    const analysis = JSON.parse(textBlock.text);

    return NextResponse.json({ analysis });
  } catch (err) {
    console.error("Extraction error:", err);
    const message =
      err instanceof Error ? err.message : "Failed to extract bid data";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
