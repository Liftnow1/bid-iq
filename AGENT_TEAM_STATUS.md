# Liftnow Agent Team — Status Report

**Date:** 2026-05-28
**Prepared for:** Cross-instance Claude handoff (so another Claude session can pick up coherently)
**Author:** Claude session in `bid-iq` repo

---

## Executive summary

- **4 marketing agents active** (Turtle, Owl, Bee, Eagle) + 9 infrastructure workflows. **23 unproven agents are OFF.**
- **End-to-end chain proven LIVE today:** Turtle detects decay → Bee/Owl draft → human approves in HubSpot dashboard → Approval Executor (AE) patches WordPress → integrity check confirms or rolls back.
- **First real autonomous WP change:** WC product category 123 (`/products/vehicle-lifts/`) had its name + description PATCHed via AE. Verified via WP REST API + live page.
- **Open issue:** Yoast SEO title/description for product_cat targets requires a custom mu-plugin (`yoast-rest-term-meta.php`) Paul installed today. Initial v1 had wrong internal key names (`title` instead of `wpseo_title`); v2 fixed the mapping. Awaiting fresh test after Paul's latest upload.
- **Today's biggest find:** Bee was hallucinating model numbers (HD-18, CLHM-150). Now wired to `/api/known-models` which pulls from bid-iq KB. QA gate rejects any draft mentioning models not in the KB.

---

## Architecture

```
┌──────────┐  cron     ┌─────────────────┐  HubSpot   ┌─────────────────┐
│  Agents  ├──────────►│   Ticket  filed │───────────►│   HubSpot CRM   │
│ (n8n)    │           │   (stage 1)     │            │ (pipeline 0)    │
└──────────┘           └─────────────────┘            └────────┬────────┘
                                                                │
                                                                ▼
                                                      ┌─────────────────────┐
                                                      │ Approvals dashboard │
                                                      │ bid-iq-neon.vercel  │
                                                      │ /approvals          │
                                                      └─────────┬───────────┘
                                                                │ user approves
                                                                ▼
┌──────────────────────────────┐         ┌─────────────────────────────────┐
│  process-decision webhook    │────────►│ HubSpot ticket → stage 2        │
└──────────────────────────────┘         └─────────────────┬───────────────┘
                                                            │ 5-min cron
                                                            ▼
                                          ┌───────────────────────────────┐
                                          │ Approval Executor (n8n)       │
                                          │  Fetch stage-2 → Process &    │
                                          │  Route → Switch by action_type│
                                          │  → executor branch → Mark     │
                                          │  Tickets Executed (integrity  │
                                          │  check) → PATCH Ticket Stage  │
                                          │  → Auto-Applied (1363043699)  │
                                          └───────────────┬───────────────┘
                                                          │
                              ┌───────────────────────────┼─────────────────────────┐
                              ▼                           ▼                          ▼
                       ┌──────────────┐         ┌──────────────────┐       ┌──────────────────┐
                       │ WP PATCH     │         │ Publish to WP    │       │ Send Email (SMTP)│
                       │  - posts/    │         │  (new content)   │       │  (peer outreach) │
                       │  - pages/    │         └──────────────────┘       └──────────────────┘
                       │  - product_  │
                       │    cat (name │
                       │    + desc)   │
                       └──────┬───────┘
                              │ if product_cat
                              ▼
                       ┌──────────────────────────┐
                       │ PATCH Yoast Term Meta    │
                       │ /liftnow/v1/yoast-term-  │
                       │ meta (custom endpoint    │
                       │ in mu-plugin)            │
                       └──────────────────────────┘
```

---

## Per-agent status

### Marketing agents (active)

#### 🐢 Turtle — Content Decay Detector
- **Workflow ID:** `yYyj4TnP9Ho9O85l`
- **Schedule:** Weekly Mon 7AM ET (now has manual webhook `/webhook/fire-content-decay`)
- **Job:** Compares GSC last-28d vs prior-28d. Files tickets for pages losing >40% clicks or >5 position drops.
- **Output ticket shape:** `agent_name="Content Decay Detector"`, `recommendation_type="Content Refresh"`, `recommendation_detail={url, click_drop_pct, position_drop, prev_clicks, cur_clicks}`
- **Cross-agent handoff:** When approved, AE writes `HANDOFF:{"to":"Owl","payload":{kind:"refresh_url",url:...}}` into outcome_notes. Owl reads this on next cycle.
- **Status:** ✅ Working. Verified files real tickets from live GSC data (ticket #45562937985, #45622618243).
- **Known limitation:** Won't pick up the same URL twice within 30 days (decision memory).

#### 🦉 Owl — Content Producer
- **Workflow ID:** `d7YwC4ezub4g1LrI`
- **Schedule:** Tue/Wed/Thu/Fri 9/13/17 ET (3x/day) + manual webhook `/webhook/fire-content-producer`
- **Job:** Fills the editorial calendar with cluster pages anchored to /resources/ pillars. Also reads Turtle's refresh queue (last 14d closed Turtle tickets with HANDOFF: marker) and prioritizes those URLs.
- **Output ticket shape:** `agent_name="Content Producer"`, `recommendation_type="Content Draft"`, `recommendation_detail={pick:{title,...}, draft_preview}`
- **Cross-agent handoff:** Reads Turtle's refresh queue at start of cycle. Picks Liftnow URLs only (skips competitor-brand URLs via filter).
- **Status:** ✅ Working. Verified: picks up Turtle handoffs, voice/identity gate correctly rejects Hunter/Mohawk-branded URLs (e.g., `/product/hunter-dsp706-passenger-aligner/`).
- **Known limitation:** Self-Reject Gate (12 checks) is strict — fails on missing fleet-manager callout, missing procurement callout, banned competitor mention, etc. Often skips when LLM draft is too generic.

#### 🐝 Bee — SEO Optimizer
- **Workflow ID:** `N03TEmB50zG0XiiP`
- **Schedule:** Daily 6AM ET + manual webhook `/webhook/fire-seo-optimizer`
- **Job:** Crawls WP pages, posts, AND WC product categories. Pulls GSC 90d data. Identifies opportunities (PAGE_2_STRUGGLE, HIGH_IMP_LOW_CTR, SLEEPING_GIANT, INDEX_GAP). For each, drafts new title/meta/H2/section body via Claude. Files HubSpot ticket for approval.
- **Output ticket shape:** `agent_name="SEO Optimizer"`, `recommendation_type="Content Optimization"`, `recommendation_detail={wpId, url, wp_type, opportunity, patch:{title, excerpt, new_h2, new_section_body}}`
- **Today's structural changes:**
  - Added `Get All Product Categories` + Page 2 fetches (paginates beyond 100)
  - Added `Fetch Known Models` (calls `/api/known-models`) — KB-grounded product list
  - Added redirect-detection via `/api/check-redirect` Vercel endpoint (n8n sandbox can't detect HTTP redirects natively)
  - When URL 301s to a WC product category, Bee re-targets the destination (wpId, slug, wp_type all updated; falls back to direct `/wp/v2/product_cat?slug=X` fetch if destination not in initial pool)
  - Build LLM Payload now injects **AUTHORITATIVE PRODUCT MODELS** block into Claude system prompt — only real KB models allowed
  - Parse + QA scans body for model strings — fails QA with `UNKNOWN_MODELS:` if any aren't in the KB
- **Status:** ✅ Working through ticket filing. **The Yoast meta write (custom mu-plugin endpoint) is the open piece — pending fresh verification after Paul's v3 plugin upload.**
- **Known limitation:** WP theme suppresses category description display (the `<header class="woocommerce-products-header">` is empty site-wide). That's why we have to route Yoast title/desc through the custom endpoint — that's the field that DOES render.

#### 🦅 Eagle v3 — UI/UX Performance
- **Workflow ID:** `alik1C8sXr857rY7`
- **Schedule:** Mon 7AM ET + manual webhook `/webhook/fire-uiux`
- **Job:** Crawls /resources/ pages, fetches rendered HTML, scans for missing alt text, missing H1, broken internal links, slow page load signals. Auto-PATCHes safe defects directly (no approval needed for trivial fixes like alt text). Files summary ticket if auto-fixes applied.
- **Output ticket shape:** `agent_name="UI/UX Performance"`, `recommendation_type="Design Audit"`, `recommendation_detail={action_type:"eagle_run_log", auto_fixed_count, pages_auto_fixed}`
- **Status:** ✅ Working. Auto-applies trivial fixes without human approval.
- **Known limitation:** Only patches WP posts/pages, not product_cat. Larger structural changes still file a ticket.

### Infrastructure workflows (active)

| Workflow | ID | Role |
|---|---|---|
| Approval Executor (Forge/Beaver) | `hpgbBAmRmqtsfr6g` | Central. Reads stage-2 tickets, routes by action_type, executes, integrity-checks, marks Auto-Applied (`1363043699`) or rolls back to stage 1 with error. |
| Webhook: Process Decision (write) | `NAjdXCnFDyV5Z4Ep` | `/webhook/process-decision` — UI calls this when user approves/rejects. Moves ticket to stage 2 or 4. |
| Webhook: Agent Proposals (read) | `VY38KRldIFDdvKJj` | `/webhook/agent-proposals` — dashboard reads pending tickets. |
| Webhook: Decision Memory | `Oq07XLN678zlAIaF` | `/webhook/decision-memory` — agents query past approval/rejection patterns. Currently GET-only (lookup), not write. |
| Heartbeat Aggregator | `liOYeGcSZD8UQEOX` | 5-min cron. Pulls recent executions, sends heartbeats to dashboard so user sees agent liveness. |
| Agent Heartbeat Store | `P3fKgDjcHFIBwHgI` | Webhook receiver for heartbeats. |
| Agent Feedback Store | `P4qZX27Up0Hs9rcE` | Webhook receiver for user feedback on agent outputs. |
| Master Kill Switch | `ljEP1fIxSr2UmJHA` | Emergency stop for all auto-mutate agents. |
| CF7 Form → HubSpot Contact | `P7c1fwvrmRV5CTrd` | Non-agent. Liftnow.com contact form → HubSpot lead. |

### Deactivated (23 workflows)

All paused pending proof. Re-activate one at a time after audit:
- Maverick (SEM Auto), Tally (SEM Manager), Hound (Keyword Discovery)
- Spider (Backlink Builder), Beacon (LinkedIn Cadence), Crow (Reddit Submissions)
- Reed (Community Engagement / Bat-Reply), Cat (Competitor SERP), Nox (Brand Listening)
- Coordinator workflows (Daily Briefing, Weekly Summary, Pattern Detector, Team Manager)
- Silent Failure Detector, Agent Failure Alerts, Duplicate Workflow Detector, Content Dup Sentinel
- Asset Review Gate, Daily ROI Tracker, SEO Keyword Rank Tracker, Site Audit Monitor, Ticket Triage Digest

---

## Critical bugs found + fixed today

### 1. **Mark Tickets Executed used `id=0`** (root cause of "going in circles" feel)
- **Symptom:** Every approved ticket got PATCHed to `tickets/0`, returning 404. Ticket stayed in stage 2 forever. AE re-processed same zombie tickets every 5 min.
- **Why hidden:** `$json.ticketId` was being overwritten by each HTTP branch's response body. Mark node saw the WP response, not the original Process & Route output.
- **Fix:** Replaced HTTP node with Code node that reads from `$('Write Handoffs').all()` directly + chains to a new `PATCH Ticket Stage` HTTP node. Integrity check now verifies no `rest_*` error in branch responses before marking Auto-Applied.

### 2. **Switch had no fallback** (auto_ack items vanished silently)
- **Symptom:** Tickets with `action_type='auto_ack'` (Turtle, Hound, brand listening) routed to nowhere. Mark/PATCH never ran.
- **Fix:** Added `fallbackOutput: 'extra'` to Route by Action Switch, wired to Merge All Branches.

### 3. **Wrong HubSpot stage label** (claiming "Done" when stage was actually "Deferred")
- **Symptom:** Setting `hs_pipeline_stage='3'` puts tickets in "Deferred" state, not "Done".
- **Fix:** HubSpot's actual stage IDs: 1=Pending Review, 2=Approved, 3=Deferred, 4=Rejected, **1363043699=Auto-Applied**. Mark now uses `1363043699`.

### 4. **n8n can't detect HTTP redirects natively**
- **Symptom:** Bee was patching `types-of-vehicle-lifts/` (a redirect-zombie post) — DB changed but live URL redirected to `/products/vehicle-lifts/`.
- **Why hidden:** n8n's `httpRequest` helper silently follows redirects even with `maxRedirects:0`. Native `fetch` isn't in Code sandbox. `require('https')` is disallowed.
- **Fix:** Built Vercel endpoint `/api/check-redirect` (uses Node `fetch` with `redirect:'manual'`). Bee calls it to detect 301s, then re-targets the destination.

### 5. **Bee invented model numbers** (HD-18, CLHM-150, etc.)
- **Symptom:** Claude drafted plausible-sounding part numbers that don't exist.
- **Why hidden:** LLM had Liftnow voice rules + Sourcewell context but no authoritative product list.
- **Fix:** Built `/api/known-models` endpoint that returns real models from bid-iq KB. Bee fetches it on each run, injects as authoritative list in system prompt. QA gate scans body for model strings and rejects unknown ones.

### 6. **WC theme suppresses category description display sitewide**
- **Symptom:** PATCHing WC `description` field via REST succeeded but content was invisible on the page. Theme renders empty `<header class="woocommerce-products-header">`.
- **Confirmed not specific to our patch:** All 2 categories with pre-existing descriptions (Heavy-Duty Lifts, In-Ground Lifts) also don't render.
- **Workaround:** Patch Yoast term meta instead (`<title>` + `<meta description>`). Requires custom mu-plugin (`yoast-rest-term-meta.php`) that exposes Yoast's internal storage to REST. Plugin uploaded by Paul; v3 with correct key mapping (`title` → `wpseo_title`, `metadesc` → `wpseo_desc`) is latest.

---

## Open items

1. **Verify v3 mu-plugin works end-to-end.** Next step: fire Bee fresh, watch it produce a ticket, approve, see if `yoast_head_json.title` actually changes on category 123.

2. **WP plugin upload automation.** Paul currently has to manually upload `.php` files to `/wp-content/mu-plugins/`. See recommended options at top of this doc — Code Snippets plugin is the lowest-friction path.

3. **Re-activate deactivated agents one by one.** Each needs:
   - End-to-end proof (file ticket → approve → execute → verify)
   - Integrity check (downstream errors detected, ticket rolled back)
   - HubSpot ticket shape documented

4. **UI rewrite (3 tabs)** is deployed at https://bid-iq-neon.vercel.app/approvals. Paul still finds it lacking — needs another pass after backend is stable.

5. **Content body rendering for product_cat.** Yoast meta covers title/description (what Google sees). Long-form body content (the 2800-char section Bee drafted) doesn't appear because theme suppresses term description. Options: (a) theme dev adds `term_description()` call to category template (one line), or (b) Bee creates a separate blog post linked from the category instead.

---

## Repository layout (for the other Claude instance)

```
bid-iq/
├── app/
│   ├── api/
│   │   ├── ask/              # KB Q&A (Claude with system prompt)
│   │   ├── check-redirect/   # NEW today — Bee's redirect detection
│   │   ├── known-models/     # NEW today — KB-grounded product list
│   │   ├── reddit-search/    # Brave API proxy (Reddit cloud-blocked)
│   │   ├── products/         # WC product catalog
│   │   └── ...
│   ├── approvals/            # /approvals dashboard (3-tab rewrite)
│   └── knowledge-base/       # KB management UI
├── lib/
│   └── db.ts                 # Neon Postgres — brands + knowledge_items schema
├── .tmp_n8n/                 # Scripts that PUT workflow JSON to n8n API
├── .tmp_wp/                  # PHP plugin files to upload to WP
│   └── yoast-rest-term-meta.php  # The mu-plugin Paul just uploaded
├── CLAUDE.md                 # Project instructions (read this first)
└── AGENT_TEAM_STATUS.md      # ← THIS FILE
```

## Conventions

- **Brand name:** `Liftnow` (lowercase `n`, one word). Never `LiftNow` or `LIFTNOW`. Hard rule.
- **Kill-list (Bee NEVER positions as Liftnow products):** Mohawk, Stertil-Koni, Ari-Hetra, Hunter, Rotary, Snap-on, Whip, Gray, Challenger (✗ scratch — Challenger IS one of Liftnow's vendors; the kill-list applies to brands Liftnow does NOT carry as Liftnow products. Confirmed via `mcp__bidiq__list_brands` — `challenger` has `we_carry=true, relationship_type='own_vendor'`.)
- **Real vendor brands (we_carry=true):** liftnow, bendpak, alemite, challenger, pks, coats, omer, balcrank, mahle, champion, lincoln, mattei, pro-cut
- **No pricing in public posts.** Hard rule enforced by Bee's QA gate.
- **Email recipient for agent reports:** `paulj@liftnow.com` (not paul.joseph.stern@gmail.com).
- **Shared secret for webhooks:** `LiftnowAgentTeam_kS9-bGq2_xVtN4mP` (header `x-agent-secret`)
- **n8n API key (JWT):** stored in operator env as `N8N_API_KEY`. Workflows live at `https://agents.liftnowdirect.com`.
- **WP credentials:** `httpBasicAuth` credential ID `AZ9RGlvZiPcT3wDJ` (user: `liftnow-agent`, now has Shop Manager role for WC perms).
- **HubSpot credential ID:** `S7YZNtmkHgtF72gN` (Private App, scoped to tickets).
- **Pipeline stages on HubSpot ticket pipeline 0:**
  - `1` = Pending Review (needs human)
  - `2` = Approved (AE picks up)
  - `3` = Deferred
  - `4` = Rejected
  - `1363043699` = Auto-Applied (AE executed successfully)

---

## How to coordinate with the other Claude

If your other Claude is making changes to the agent system or Bee specifically:

- **Before adding a new node to Bee or AE:** check this file's "Per-agent status" for current structure. Bee has redirect remap → KB models lookup → LLM draft → QA → ticket. Don't break the chain by inserting in the wrong order.
- **Before changing the LLM prompt in Bee:** the system prompt now has 3 hard rules stacked: voice (Paul Stern), no-pricing, KB-grounded models. Keep all three.
- **Before changing how AE PATCHes WP:** `wp_patch` branches by `wpPatchType` (`pages`, `posts`, `product_cat`). product_cat splits into two HTTP calls: standard PATCH for name+description, custom endpoint for Yoast term meta.
- **Before turning on a deactivated agent:** read its workflow JSON, confirm it has continueOnFail on any node that could fail mid-chain, confirm its action_type maps to a real branch in AE's Process & Route, and that there's no $json clobbering between Process & Route and Mark Tickets Executed.
- **HubSpot ticket conventions:** the `recommendation_detail` field is a JSON string (not object). AE parses it. The `outcome_notes` field carries handoff payloads via the `HANDOFF:{json}` marker.

This file is committed to the repo. Re-read when starting a new session.
