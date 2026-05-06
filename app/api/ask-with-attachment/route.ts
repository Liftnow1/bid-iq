// File-upload Q&A endpoint (iter13).
//
// User uploads a document/image plus a question. The endpoint:
//   1. Runs the user's question through the existing /api/ask retrieval
//      pipeline to pull supporting KB context.
//   2. Sends a SECOND Claude call with:
//        - the uploaded file as a document/image content block (Claude
//          handles native PDF text + vision automatically since
//          claude-3.5-sonnet 20241022)
//        - the KB answer + cited sources as text context
//        - the user's question
//   3. The system prompt tells Claude that the UPLOAD is the primary
//      subject of the question (option (a) per Paul's directive); the
//      KB context is supporting reference material from Liftnow's
//      archives, to be used only if relevant.
//
// The upload is session-scoped — never persisted or ingested into the
// KB. That's a separate flow (the daily watcher in scripts/).
//
// Supported file types:
//   PDF              → document content block (native text + vision)
//   PNG/JPG/GIF/WEBP → image content block (vision)
// Other types are rejected with a 415.

import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";

// Same long-running cap as /api/ask — vision can be slow on large PDFs.
export const maxDuration = 300;

const SYSTEM_PROMPT_FOR_UPLOAD = `You are a strict factual research assistant for Liftnow, a government-focused dealer of vehicle lifts and heavy equipment maintenance gear.

The user has uploaded a document or image (attached as the first content block in the user message). The UPLOAD is the PRIMARY subject of the user's question — read it carefully first.

You have ALSO been given context from Liftnow's internal knowledge base (KB) as text below the upload. The KB context is SUPPORTING reference material — use it ONLY when it's directly relevant to the upload and the user's question.

## How to weigh the two contexts

- **Upload first.** When the user asks "what does this say about X?", "summarize this", "what model is this?", "is this lift compliant?", "what does this contract require?" — the answer comes from the UPLOAD itself.
- **KB second.** When something in the upload references a specific Liftnow product, contract, or domain term that the user wants more info about — pull from the KB context. Cite KB sources by their [N] index, e.g. "[3]".
- **Don't conflate them.** If the upload mentions a CL12A and the KB has CL12A specs, you can synthesize the two ("the upload references CL12A, which per the KB has these specs [3]"). But if the upload is about a totally different topic from the KB hits, just ignore the KB.

## Rules

- **Brand name.** Liftnow is lowercase \`n\`, one word. Never "LiftNow" or "LIFTNOW".
- **No fabrication.** Don't invent contents of the upload. If a page is unreadable or you can't find specific info, say so explicitly.
- **Per-fact citation rule for KB sources.** When you reference a fact pulled from the KB, cite the source number in brackets. Facts pulled from the upload don't need a citation.
- **No contact info from KB.** Same redaction rules as the regular Q&A. Phones, emails, and street addresses from KB sources are redacted; do not invent them.
- **Refusal.** If the upload is unreadable AND the KB has nothing relevant, say so explicitly rather than padding with general industry knowledge.

The user's question follows the contexts.`;

const SUPPORTED_PDF_TYPES = new Set(["application/pdf"]);
const SUPPORTED_IMAGE_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/jpg",
  "image/gif",
  "image/webp",
]);

// Cap the upload size. Anthropic's API limits document attachments to
// ~32 MB; we cap lower to keep the prompt construction snappy and to
// reject pathological files early.
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024; // 25 MB

type AskResponse = {
  answer?: string;
  sources?: Array<{
    index: number;
    cited: boolean;
    id: number;
    title: string;
    source_filename: string | null;
    source_path: string | null;
    summary: string | null;
    brand_name: string | null;
  }>;
  query_mode?: string;
  error?: string;
};

export async function POST(request: NextRequest) {
  try {
    const formData = await request.formData();
    const question = String(formData.get("question") ?? "").trim();
    const file = formData.get("attachment");

    if (!question) {
      return NextResponse.json(
        { error: "No question provided" },
        { status: 400 }
      );
    }
    if (!(file instanceof File)) {
      return NextResponse.json(
        { error: "No attachment uploaded (field name must be 'attachment')" },
        { status: 400 }
      );
    }
    if (file.size === 0) {
      return NextResponse.json(
        { error: "Uploaded file is empty" },
        { status: 400 }
      );
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      return NextResponse.json(
        {
          error: `File too large (${file.size} bytes). Max is ${MAX_UPLOAD_BYTES} bytes (25 MB).`,
        },
        { status: 413 }
      );
    }

    const mediaType = file.type || "application/octet-stream";
    const isPdf = SUPPORTED_PDF_TYPES.has(mediaType);
    const isImage = SUPPORTED_IMAGE_TYPES.has(mediaType);
    if (!isPdf && !isImage) {
      return NextResponse.json(
        {
          error: `Unsupported file type: ${mediaType}. Supported: PDF, PNG, JPEG, GIF, WEBP.`,
        },
        { status: 415 }
      );
    }

    // Read the upload into base64 once — used as the document/image content
    // block in the Claude call below.
    const arrayBuf = await file.arrayBuffer();
    const base64 = Buffer.from(arrayBuf).toString("base64");

    // Step 1: pull KB context using the existing /api/ask retrieval. We
    // call it via internal fetch to avoid duplicating ~2K lines of search
    // logic. The internal call uses the same NEXT.js host the request hit.
    const origin = request.headers.get("origin") ?? request.nextUrl.origin;
    const cookieHeader = request.headers.get("cookie") ?? "";
    let kbAnswer = "";
    let kbSources: NonNullable<AskResponse["sources"]> = [];
    try {
      const kbRes = await fetch(`${origin}/api/ask`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // Forward Vercel deployment auth cookies so the internal call
          // doesn't 401 on protected previews.
          ...(cookieHeader ? { Cookie: cookieHeader } : {}),
        },
        body: JSON.stringify({ question }),
      });
      const kbData = (await kbRes.json()) as AskResponse;
      if (kbRes.ok) {
        kbAnswer = kbData.answer ?? "";
        kbSources = kbData.sources ?? [];
      }
    } catch (e) {
      // Non-fatal — proceed without KB context.
      console.warn("ask-with-attachment: internal /api/ask call failed:", e);
    }

    // Format KB context as a readable text block for Claude. Show only
    // the cited sources (matches the UI behavior; uncited noise filtered).
    const citedSources = kbSources.filter((s) => s.cited !== false);
    const kbContextLines: string[] = [];
    if (kbAnswer) {
      kbContextLines.push("## KB context (supporting reference)");
      kbContextLines.push("");
      kbContextLines.push(
        "The Liftnow knowledge base was queried with the user's question. " +
          "The retrieved answer follows. Use this only if it's relevant to " +
          "the uploaded document; the upload itself is the primary subject."
      );
      kbContextLines.push("");
      kbContextLines.push("### KB answer:");
      kbContextLines.push(kbAnswer);
      if (citedSources.length > 0) {
        kbContextLines.push("");
        kbContextLines.push("### KB sources cited:");
        for (const s of citedSources) {
          const fname = s.source_filename || s.title;
          const brand = s.brand_name ? ` [brand=${s.brand_name}]` : "";
          kbContextLines.push(`[${s.index}] ${fname}${brand}`);
          if (s.summary) {
            kbContextLines.push(`    ${s.summary.slice(0, 240)}`);
          }
        }
      }
    } else {
      kbContextLines.push(
        "## KB context: no Liftnow KB results matched the question text alone. " +
          "Answer from the upload only."
      );
    }
    const kbContext = kbContextLines.join("\n");

    // Build the Claude call with the upload as the first content block.
    const client = new Anthropic();
    const userContent: Anthropic.MessageParam["content"] = [];

    if (isPdf) {
      userContent.push({
        type: "document",
        source: {
          type: "base64",
          media_type: "application/pdf",
          data: base64,
        },
      });
    } else if (isImage) {
      userContent.push({
        type: "image",
        source: {
          type: "base64",
          media_type: mediaType as
            | "image/png"
            | "image/jpeg"
            | "image/gif"
            | "image/webp",
          data: base64,
        },
      });
    }
    userContent.push({
      type: "text",
      text: `${kbContext}\n\n## User's question:\n${question}`,
    });

    const completion = await client.messages.create({
      model: "claude-sonnet-4-5",
      max_tokens: 4096,
      system: SYSTEM_PROMPT_FOR_UPLOAD,
      messages: [{ role: "user", content: userContent }],
    });

    const answerText = completion.content
      .filter((b) => b.type === "text")
      .map((b) => (b as Anthropic.TextBlock).text)
      .join("\n");

    return NextResponse.json({
      answer: answerText,
      attachment: {
        filename: file.name,
        size: file.size,
        media_type: mediaType,
      },
      kb_context: {
        answer: kbAnswer,
        sources: citedSources,
      },
      query_mode: "with-attachment",
    });
  } catch (err) {
    console.error("ask-with-attachment error:", err);
    const message =
      err instanceof Error ? err.message : "Failed to process upload";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
