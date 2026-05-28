# Audit 04 — Circular Logic & $json Clobbering Scan

**The id=0 pattern catalog.** Every place where `$json.X` is read downstream of
an HTTP node whose response body could overwrite `X`.

## Scan method

Read all 5 active workflow JSONs from `audit/data/workflows/`. For each node:
- If type is `httpRequest`, treat its output as **clobbering** $json (the HTTP
  response body replaces $json downstream)
- Find all downstream nodes that read `$json.X` where X is unlikely to exist
  in an HTTP response body (e.g., `ticketId`, `wpPatchPageId`, `wpPatchType`,
  `agentName`)
- Flag each as a circular-logic risk

## Confirmed instances (P0 to fix)

### 🦅 EAGLE — `Fetch Rendered HTML` reads `$json.fetchedUrl`

```
[Eagle exec 2026-05-27T15:11:40]
ERROR: Fetch Rendered HTML: URL parameter must be a string, got undefined
```

Eagle's `Fetch Rendered HTML` URL expression: `={{ $json.fetchedUrl }}`. Upstream is likely an HTTP node (Get Page Inventory or similar) whose response body doesn't have a `fetchedUrl` field. Clobbered.

**Fix:** change to `$('UpstreamCodeNode').itemMatching($itemIndex).json.fetchedUrl` — wherever the original page list was constructed (likely a Code node like Filter Content Pages does in Bee).

### 🦉 OWL — `Fetch Refresh Queue` uses wrong credential type

Not strictly a $json clobber, but same family of "auth/cred used wrong type":
```
ERROR: Credential with ID "S7YZNtmkHgtF72gN" does not exist for type "hubspotApi".
```

The credential is registered as `httpHeaderAuth`. The node was wired with `nodeCredentialType: 'hubspotApi'`. Fix: change to `httpHeaderAuth`.

## Already-fixed (yesterday's session, verified)

### ⚙️ AE — `Mark Tickets Executed` was using `$json.ticketId` → id=0

Previously the URL expression was `https://api.hubapi.com/crm/v3/objects/tickets/{{ $json.ticketId || '0' }}`. After multiple branches (WP PATCH, Send Email, etc) each replacing $json, ticketId was gone. Now reads from `$('Write Handoffs').all().map(i => i.json)` — pulls from the named upstream Code node.

**Verified:** ae.json (audit/data/workflows/ae.json) shows current Mark Tickets Executed code reads from `$('Write Handoffs').all()` via type=code with named-node refs.

### 🐝 BEE — `Build LLM Payload` redirect-check (5 iterations)

Yesterday's loop tried `httpRequest({maxRedirects:0})`, `fetch()`, `require('https')` — all blocked by n8n sandbox. Final fix was `/api/check-redirect` Vercel proxy. Now stable.

## High-risk patterns to AUDIT (need code inspection)

### ⚙️ AE — `WP PATCH Page` body references `$json.wpPatchPageId`

The body expression: `={{ JSON.stringify((function() { const j = $json; if (j.wpPatchType === 'product_cat') ... })()) }}`.

**Risk:** This is downstream of `Route by Action` (Switch). The Switch doesn't clobber $json — it just routes the item. BUT if Process & Route ever stops setting `wpPatchPageId` (e.g., for a new action_type that misses the field), this would PATCH `tickets/undefined`.

**Status:** SAFE today because Process & Route always sets `wpPatchPageId` for wp_patch items. Add defensive default and zod validation in P2 (item #8 of the prompt).

### ⚙️ AE — `PATCH Yoast Term Meta` already uses `$('Process & Route').itemMatching($itemIndex)`

Yesterday's fix. Confirmed safe.

### 🐝 BEE — `Create SEO Ticket` reads `$('Self-QA').first().json.subject`

Named-node ref, safe.

### 🦉 OWL — `Create Content Ticket` body references `$json.title`, `$json.draft_preview`

Coming from upstream Code nodes (Self-Reject Gate then Self-Reject Passed?). Code nodes don't clobber $json. SAFE.

## Net risk inventory

| Workflow | Node | $json.X | Upstream type | Risk |
|---|---|---|---|---|
| Eagle | Fetch Rendered HTML | `fetchedUrl` | HTTP | 🔴 CONFIRMED |
| Owl | Fetch Refresh Queue | (credential type) | n/a | 🔴 CONFIRMED |
| AE | WP PATCH Page | `wpPatchPageId` | Switch | 🟡 Latent |
| AE | PATCH Yoast Term Meta | uses $node ref | n/a | ✅ Safe |
| AE | Mark Tickets Executed | uses $node ref | n/a | ✅ Safe |
| Bee | Create SEO Ticket | `$('Self-QA')` ref | n/a | ✅ Safe |
| Owl | Create Content Ticket | upstream is Code | Code | ✅ Safe |
| Owl | Generate Draft + LinkedIn | upstream is Code | Code | ✅ Safe |

## Systemic recommendation (P2 item)

Per the prompt's Section 5 — every Build/Fix step requires the circular-logic check. Codify this:
- Lint rule: any `$json.X` in an HTTP node body/URL where the immediate upstream is also an HTTP node = warning
- For new workflows, default to `$('NodeName').itemMatching($itemIndex).json.X` syntax
- Document in CLAUDE.md as a coding standard
