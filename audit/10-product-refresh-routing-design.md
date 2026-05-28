# Design — Route product-page refreshes to Bee (not Owl)

**Decision (Paul, 2026-05-28):** decaying *product pages* should be refreshed by **Bee** (on-page SEO: title/meta/description), not **Owl** (long-form articles). Owl's article gate (byline/TOC/FAQ/1500+ words) doesn't fit a product page, so today those refreshes self-reject.

## Problem recap
- Turtle (Content Decay Detector) flags decaying pages → AE Process & Route currently ALWAYS hands refreshes to Owl (`handoffTo='Owl'`, to_agent='Content Producer').
- Owl is an article writer → product-page refreshes fail its gate.
- Bee already optimizes product_cat pages (title/meta/description/Yoast) but only via its own crawl-discovery; it has no way to take a *targeted* page from a queue.

## Classification rule
A refresh target is a **product page** when its URL matches `/product/` or `/products/` (WooCommerce single product or product category). Everything else (`/resources/`, `/blog/`, root content) stays an **article** → Owl.

## Change 1 — AE Process & Route (Turtle branch) [SMALL]
In the `Content Decay Detector` branch, classify the URL and set the destination:
```js
const _refreshUrl = recDetail.url || '';
const _isProductPage = /\/products?\//i.test(_refreshUrl);
item.handoffTo = _isProductPage ? 'Bee' : 'Owl';
```
`Write Handoffs` already maps nickname→role: `Bee → SEO Optimizer`. So product-page refreshes land in `agent_handoffs` with `to_agent='SEO Optimizer'`.

## Change 2 — Bee targeted mode [MEDIUM]
Bee gains a "refresh queue first, else crawl" pattern (mirrors Owl):

a. **New fetch node** `Fetch SEO Refresh Queue` (HTTP GET `/api/agent-handoffs?to_agent=SEO%20Optimizer&pending=true`, `executeOnce:true`, `neverError`, `onError:continueRegularOutput`). Insert in the chain: `… Fetch Known Models → Fetch SEO Refresh Queue → Filter Content Pages`.

b. **Diagnostic Classifier — targeted branch (prepended):**
```js
const rq = (()=>{try{return $('Fetch SEO Refresh Queue').first().json;}catch(e){return null;}})();
const ho = ((rq&&rq.handoffs)||[]).find(h=>h.kind==='refresh_url' && h.payload && h.payload.url);
if (ho) {
  const turl = ho.payload.url;
  const norm = u => (u||'').replace(/\/+$/,'').toLowerCase();
  const match = pages.find(p => norm(p.url)===norm(turl));
  const base = match || { url:turl, wpId: ho.payload.wpId||0, title: ho.payload.title||turl, wp_type:'product' };
  return [{json:{ ...base, opportunity:'REFRESH_REQUESTED',
    diagnosis:'Refresh requested by Content Decay Detector (decaying product page)',
    severity:999, refresh_handoff_id: ho.id, _targeted:true }}];
}
// else: existing crawl-discovery logic (unchanged)
```
This makes the requested page the SOLE candidate, so Bee optimizes exactly that page. (If the page isn't in Bee's page/post/product_cat inventory — e.g. a `/product/` single product — it still emits a minimal candidate; Build LLM Payload's redirect-remap + WC-slug fetch already handles resolving product_cat by slug.)

c. **Consume the handoff** after `Create SEO Ticket`: new HTTP node `Consume SEO Handoff` → POST `/api/agent-handoffs/{{refresh_handoff_id}}/consume`, wired from BOTH `Create SEO Ticket` (success) and `Log No Opportunity` (so a targeted page that yields no change still clears the queue). `onError:continueRegularOutput`, reads id from `$('Diagnostic Classifier').first().json.refresh_handoff_id`.

## Change 3 — Hunter rule in Bee [SMALL]
Mirror the Owl change so Bee can refresh Hunter product pages without co-op claims:
- **Build LLM Payload VOICE prompt:** add the competitor-brand HARD RULE (name Hunter OK on Hunter pages; OMIT Sourcewell/NASPO/FSA; other kill-list brands off-limits).
- **Parse + QA `banned` regex:** make Hunter-aware — when the page URL contains `hunter`, don't reject on the "Hunter" token (keep the other brands banned).

## Build order (no stranded handoffs)
1. Build Bee side first (Changes 2 + 3) and verify it consumes a synthetic `to_agent='SEO Optimizer'` handoff and optimizes the page.
2. THEN flip AE routing (Change 1) so product refreshes start flowing to Bee.

## Test plan (receipts)
- Seed `agent_handoffs` row: to_agent='SEO Optimizer', kind='refresh_url', url=`/products/vehicle-lifts/` (non-Hunter product_cat) → fire Bee → expect: Diagnostic Classifier targeted branch picks it, Create SEO Ticket fires for THAT page, Consume SEO Handoff marks it consumed.
- Seed a Hunter one (`/product/hunter-tc33m…`) → fire Bee → expect: optimizes it, draft names Hunter, NO Sourcewell/NASPO, ticket filed, handoff consumed.
- Approve a real Turtle product-page ticket → AE → confirm handoff lands as to_agent='SEO Optimizer' and Bee consumes it.

## Risks / guards
- Bee just had a fan-out + executeOnce fix; the new fetch node MUST be `executeOnce:true` or it re-multiplies (the bug that OOM'd Bee). 
- Keep crawl-discovery mode the default (only override when a handoff is present) so Bee's normal weekly SEO sweep is unaffected.
- `/product/` single products may not be in inventory — rely on Build LLM Payload's existing slug-fetch fallback; if it can't resolve, Bee should skip + consume (no crash).
