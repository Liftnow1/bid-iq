# Bid-iq MCP Server

Bid-iq exposes its knowledge retrieval over [MCP](https://modelcontextprotocol.io/)
(Model Context Protocol), making it queryable from Claude Code, Claude
Desktop, and any other MCP-aware client.

The endpoint lives at `/api/mcp` on the existing Next.js app (no new
infrastructure). Auth is a single bearer token; the server runs in
stateless mode so it scales on Vercel serverless without session storage.

## What's exposed

| Tool             | Purpose                                                            |
| ---------------- | ------------------------------------------------------------------ |
| `ask_bidiq`      | Wraps `/api/ask`. Takes `question` (required) + advisory `brand_filter`. Returns the answer plus a list of cited sources with brand and tier. |
| `list_brands`    | Lists every brand in the table with `we_carry` and `doc_count`. Optional `we_carry_only` flag. |
| `get_brand_info` | For one canonical brand: total doc count, source-type breakdown, the 10 most recent documents. |

`brand_filter` on `ask_bidiq` is advisory in v1 — it biases the answer
toward the named brand but does not add a hard SQL constraint to retrieval.
Hard filtering is a Phase 2 build that needs `/api/ask` itself to accept a
brand parameter.

## One-time server setup

1. **Generate a token.** Anything strong works:

   ```bash
   openssl rand -hex 32
   ```

2. **Add it locally** to `.env.local` so `npm run dev` can serve `/api/mcp`:

   ```
   BIDIQ_MCP_TOKEN=<paste the 64-char hex string>
   ```

3. **Add it to Vercel.** Dashboard → bid-iq project → Settings → Environment
   Variables → add `BIDIQ_MCP_TOKEN` with the same value, applied to
   Production and Preview. Env-var changes don't auto-redeploy; trigger a
   redeploy after adding so the running pods pick it up.

`.env.local.example` in the repo carries a placeholder; copy it to
`.env.local` and fill in the real value (don't commit `.env.local`).

## Connection: Claude Code

```bash
claude mcp add --transport http bidiq https://<your-deployed-domain>/api/mcp \
  --header "Authorization: Bearer <BIDIQ_MCP_TOKEN>"
```

Verify:

```bash
claude mcp list
# Expect a row for `bidiq`
```

Then in any Claude Code session:

> "Use the bidiq tool to ask: what does the Challenger 4018 manual say
> about anchor patterns?"

Claude Code will pick the tool, call `ask_bidiq`, and return the answer
with sources.

For local testing against `npm run dev`, swap the URL:

```bash
claude mcp add --transport http bidiq-local http://localhost:3000/api/mcp \
  --header "Authorization: Bearer $BIDIQ_MCP_TOKEN"
```

## Connection: Claude Desktop

Edit `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "bidiq": {
      "command": "npx",
      "args": [
        "mcp-remote@latest",
        "--http",
        "https://<your-deployed-domain>/api/mcp",
        "--header",
        "Authorization: Bearer <BIDIQ_MCP_TOKEN>"
      ]
    }
  }
}
```

Restart Claude Desktop. The bid-iq tools appear in the tools menu.

## Auth

The server checks `Authorization: Bearer <token>` against the
`BIDIQ_MCP_TOKEN` env var on every request. Failure modes:

- Header missing or empty → `401 {"error": "unauthorized"}`
- Header present but token doesn't match → `401 {"error": "unauthorized"}`
- Server is missing `BIDIQ_MCP_TOKEN` env var → `500` with a config
  error (intentional — fail loud rather than serve unprotected).

There is no token rotation or multi-user support. Single token,
single user. Rotate by changing the env var on Vercel and re-running
`claude mcp add` with the new value.

## Verifying with `curl`

A POST without auth:

```bash
curl -i -X POST https://<your-deployed-domain>/api/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# HTTP/1.1 401
# {"error":"unauthorized"}
```

The same with the token returns a JSON-RPC `tools/list` response showing
the three tools above.

## Out of scope (Phase 2)

- **Connecting from claude.ai web.** The web custom-connector UI requires
  OAuth, which this server does not implement. Claude Code and Claude
  Desktop both support bearer tokens natively, which is enough for the
  single-user case.
- **Multi-user auth / token rotation / RBAC.**
- **Hard brand filter on `ask_bidiq`.** Today it's advisory.
- **MCP Resources or Prompts.** Only Tools are exposed.
- **Streaming tool results** for long-running queries. Tool calls return
  a single JSON payload. The transport runs in `enableJsonResponse=true`
  mode for simplicity on Vercel serverless.
