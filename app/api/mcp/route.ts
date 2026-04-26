/**
 * MCP server endpoint for bid-iq.
 *
 * Exposes three tools — ask_bidiq, list_brands, get_brand_info — over the
 * MCP Streamable HTTP transport, gated behind a bearer-token check. See
 * BIDIQ-MCP.md for the full client wire-up.
 *
 * Auth:   Authorization: Bearer <BIDIQ_MCP_TOKEN>
 * Mode:   stateless (no session state across requests, fits Vercel
 *         serverless cleanly). enableJsonResponse=true so simple
 *         request/response tool calls don't need an SSE stream.
 */

import { NextRequest, NextResponse } from "next/server";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { z } from "zod";
import { getSQL, ensureSchema } from "@/lib/db";
import { POST as askPost } from "@/app/api/ask/route";

export const runtime = "nodejs";
// /api/ask can spend up to 5 min on auto-upgrade work; the MCP tool wraps
// it, so allow the same headroom.
export const maxDuration = 300;

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

function checkBearer(req: NextRequest): NextResponse | null {
  const expected = process.env.BIDIQ_MCP_TOKEN;
  if (!expected) {
    return NextResponse.json(
      { error: "BIDIQ_MCP_TOKEN not configured on the server" },
      { status: 500 }
    );
  }
  const header = req.headers.get("authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
  if (!token || token !== expected) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  return null;
}

// ---------------------------------------------------------------------------
// Tool: ask_bidiq
// ---------------------------------------------------------------------------

type AskResponse = {
  answer?: string;
  sources?: Array<{
    id: number;
    title: string;
    category: string;
    tier?: 1 | 2;
    brand_name?: string | null;
    source_filename?: string | null;
  }>;
  upgraded_ids?: number[];
  tier1_unupgraded_ids?: number[];
  upgrade_available?: boolean;
  query_mode?: string;
  error?: string;
};

async function askBidiq(args: { question: string; brand_filter?: string }) {
  // brand_filter is advisory in v1: we prepend it to the question rather
  // than adding a brand_id constraint to the SQL retrieval (which would
  // require modifying /api/ask, out of scope for this PR). Documented in
  // the tool description and BIDIQ-MCP.md.
  const question = args.brand_filter
    ? `[Restrict your answer to brand: ${args.brand_filter}. Cite sources from that brand only.] ${args.question}`
    : args.question;

  const askReq = new Request("http://internal/api/ask", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question }),
  });
  const askRes = await askPost(askReq as unknown as NextRequest);
  const data = (await askRes.json()) as AskResponse;

  if (askRes.status >= 400 || data.error) {
    throw new Error(data.error ?? `ask returned HTTP ${askRes.status}`);
  }

  const sources = data.sources ?? [];
  const sourcesBlock = sources.length
    ? `\n\nCited sources (${sources.length}):\n` +
      sources
        .map(
          (s) =>
            `  [${s.id}] ${s.title}` +
            (s.brand_name ? ` (brand=${s.brand_name})` : "") +
            (s.tier ? ` (tier=${s.tier})` : "") +
            (s.source_filename ? ` (file=${s.source_filename})` : "")
        )
        .join("\n")
    : "\n\n(No sources cited.)";

  return {
    content: [
      {
        type: "text" as const,
        text: (data.answer ?? "(no answer returned)") + sourcesBlock,
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Tool: list_brands
// ---------------------------------------------------------------------------

async function listBrands(args: { we_carry_only?: boolean }) {
  await ensureSchema();
  const sql = getSQL();
  const weCarryOnly = args.we_carry_only === true;

  const rows = weCarryOnly
    ? await sql`
        SELECT b.name, b.we_carry, b.relationship_type,
               (SELECT count(*)::int FROM knowledge_items ki WHERE ki.brand_id = b.id) AS doc_count
          FROM brands b
         WHERE b.we_carry = TRUE
         ORDER BY doc_count DESC, b.name ASC
      `
    : await sql`
        SELECT b.name, b.we_carry, b.relationship_type,
               (SELECT count(*)::int FROM knowledge_items ki WHERE ki.brand_id = b.id) AS doc_count
          FROM brands b
         ORDER BY b.we_carry DESC, doc_count DESC, b.name ASC
      `;

  return {
    content: [
      {
        type: "text" as const,
        text: JSON.stringify(rows, null, 2),
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Tool: get_brand_info
// ---------------------------------------------------------------------------

async function getBrandInfo(args: { brand_name: string }) {
  await ensureSchema();
  const sql = getSQL();
  const name = args.brand_name.trim();

  const brandRows = (await sql`
    SELECT id, name, we_carry, relationship_type, manufacturer_name, notes, website
      FROM brands
     WHERE lower(name) = lower(${name})
     LIMIT 1
  `) as unknown as Array<{
    id: number;
    name: string;
    we_carry: boolean;
    relationship_type: string;
    manufacturer_name: string | null;
    notes: string | null;
    website: string | null;
  }>;

  if (brandRows.length === 0) {
    return {
      content: [
        {
          type: "text" as const,
          text: JSON.stringify(
            { error: `brand not found: ${name}` },
            null,
            2
          ),
        },
      ],
      isError: true,
    };
  }

  const brand = brandRows[0];

  // source_type breakdown — falls back gracefully if the column hasn't
  // been added yet (Phase 1 of the brands-reconciliation runbook).
  let sourceTypeBreakdown: Array<{ source_type: string; n: number }> = [];
  try {
    sourceTypeBreakdown = (await sql`
      SELECT source_type, count(*)::int AS n
        FROM knowledge_items
       WHERE brand_id = ${brand.id}
       GROUP BY source_type
       ORDER BY n DESC
    `) as unknown as Array<{ source_type: string; n: number }>;
  } catch {
    sourceTypeBreakdown = [{ source_type: "(source_type column not present)", n: 0 }];
  }

  const totalRows = (await sql`
    SELECT count(*)::int AS n FROM knowledge_items WHERE brand_id = ${brand.id}
  `) as unknown as Array<{ n: number }>;

  const recent = (await sql`
    SELECT id, title, category, source_filename, extracted_at, extractor_version
      FROM knowledge_items
     WHERE brand_id = ${brand.id}
     ORDER BY coalesce(extracted_at, created_at) DESC NULLS LAST
     LIMIT 10
  `) as unknown as Array<Record<string, unknown>>;

  return {
    content: [
      {
        type: "text" as const,
        text: JSON.stringify(
          {
            brand,
            doc_count: totalRows[0]?.n ?? 0,
            source_type_breakdown: sourceTypeBreakdown,
            recent_documents: recent,
          },
          null,
          2
        ),
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Server + transport setup (per-request — stateless)
// ---------------------------------------------------------------------------

function buildServer(): McpServer {
  const server = new McpServer(
    { name: "bidiq", version: "1.0.0" },
    { capabilities: { tools: {} } }
  );

  server.registerTool(
    "ask_bidiq",
    {
      description:
        "Ask a question against the Liftnow bid-iq knowledge base. Returns " +
        "an answer with source citations from product manuals, ALI " +
        "certification metadata, and Liftnow proprietary content. " +
        "brand_filter is advisory in v1 — it biases the answer but does " +
        "not hard-filter the SQL retrieval.",
      inputSchema: {
        question: z.string().describe("The question to ask the knowledge base"),
        brand_filter: z
          .string()
          .optional()
          .describe(
            "Optional. Bias the answer toward a specific brand (lowercase " +
              "canonical folder name, e.g. 'challenger', 'mohawk')."
          ),
      },
    },
    askBidiq
  );

  server.registerTool(
    "list_brands",
    {
      description:
        "List all brands in the bid-iq knowledge base, with their " +
        "we_carry status and document counts.",
      inputSchema: {
        we_carry_only: z
          .boolean()
          .optional()
          .describe("If true, only return brands Liftnow carries."),
      },
    },
    listBrands
  );

  server.registerTool(
    "get_brand_info",
    {
      description:
        "Get details for a specific brand: total document count, " +
        "source_type breakdown, and the 10 most recent documents.",
      inputSchema: {
        brand_name: z
          .string()
          .describe("Canonical brand name (lowercase, e.g. 'challenger')."),
      },
    },
    getBrandInfo
  );

  return server;
}

async function handleMcp(req: NextRequest): Promise<Response> {
  const authError = checkBearer(req);
  if (authError) return authError;

  const server = buildServer();
  const transport = new WebStandardStreamableHTTPServerTransport({
    // Stateless: no session ID, no in-memory state across requests.
    sessionIdGenerator: undefined,
    // Plain JSON response for tool calls — no SSE stream needed for the
    // simple request/response shape we're exposing.
    enableJsonResponse: true,
  });

  await server.connect(transport);
  return transport.handleRequest(req);
}

export async function POST(req: NextRequest) {
  return handleMcp(req);
}

export async function GET(req: NextRequest) {
  return handleMcp(req);
}

export async function DELETE(req: NextRequest) {
  return handleMcp(req);
}
