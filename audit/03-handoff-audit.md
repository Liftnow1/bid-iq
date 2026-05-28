# Audit 03 — HANDOFF Marker Audit

**Method:** Searched HubSpot tickets in pipeline 0 modified in last 7 days for the literal substring `HANDOFF:` inside `outcome_notes`. Pulled via HubSpot MCP search.

## Findings

### HANDOFFs WRITTEN (by AE → outcome_notes after approval)

Searched 18 Auto-Applied tickets from 2026-05-22 through 2026-05-28.

**Confirmed handoffs in outcome_notes** (from earlier inspection in `mcp__3325496d-32c7-4c1c-b1cd-76bf7478c0d5__get_crm_objects`):

| Ticket ID | Agent | URL | Marker present? |
|---|---|---|---|
| 45562937985 | Content Decay Detector | hunter-dsp706-passenger-aligner | ✅ `HANDOFF:{"to":"Owl","payload":{"kind":"refresh_url","url":"...","click_drop_pct":-40}}` |
| 45626462750 | Content Decay Detector | hunter-tc33m-passenger-tire-changer | ✅ HANDOFF marker present |
| 45622618243 | Content Decay Detector | types-of-vehicle-lifts | ✅ HANDOFF marker present |
| 45626675540 | Content Decay Detector | types-of-vehicle-lifts | ❌ NO HANDOFF (closed BEFORE the Write Handoffs fix) |

**Pattern observed:** All Content Decay Detector tickets closed AFTER 2026-05-27 22:00 UTC have the HANDOFF marker. Earlier ones don't (orphans).

### HANDOFFs CONSUMED (by Owl reading the queue)

Owl's `Fetch Refresh Queue` node IS CURRENTLY BROKEN per audit/01 (credential type mismatch). So **zero handoffs have been successfully consumed by Owl** since the feature was wired.

Evidence: in last Owl execution that *did* succeed, Pick Next Piece showed `refresh_mode: True` and picked Hunter URL — but Owl's voice/identity gate correctly self-rejected (Hunter is on the kill-list for Liftnow-owned products positioning).

So even when handoffs CAN be read, Owl rejects them when URL contains kill-list brand name. That's correct behavior — but it also means **the current 2 handoffs in the queue (Hunter URLs) will never be consumed**.

### Orphan handoffs

| Ticket | Reason orphaned |
|---|---|
| 45562937985 (hunter-dsp706) | Voice gate would reject — Hunter is kill-list |
| 45626462750 (hunter-tc33m) | Voice gate would reject — Hunter is kill-list |
| 45622618243 (types-of-vehicle-lifts) | Liftnow URL, valid candidate — but Owl can't read queue due to credential bug |

### What HANDOFF does today (architecture audit)

1. **Write side:** `Approval Executor → Write Handoffs` (Code node, runs after Process & Route, before Switch). If `item.handoffTo` is set, it appends `HANDOFF:{json}` to `item.executionNotes`. Then `Mark Tickets Executed` reads `executionNotes` and PATCHes the HubSpot ticket's `outcome_notes` with that text.
2. **Read side:** `Owl → Fetch Refresh Queue` (HTTP node, calls HubSpot search REST: `agent_name = 'Content Decay Detector'`, `hs_pipeline_stage = '3'` OR Auto-Applied, `hs_lastmodifieddate > now-14d`). Then `Pick Next Piece` regexes the `HANDOFF:{...}` JSON out of `outcome_notes`.

### Fragility ranking

This is **P0 to replace** per the prompt's Section 4:
- ❌ Regex-based discovery (brittle to format drift)
- ❌ Mixed inside `outcome_notes` with human-readable text
- ❌ No consumed-at tracking (Owl reprocessing risk; mitigated by staticData but fragile)
- ❌ No types/validation on payload shape
- ❌ Owl filters by `agent_name = 'Content Decay Detector'` — won't extend to Hound → Tally or other future handoff pairs without per-pair code

### Recommended replacement (P0 item #2)

Per the prompt's Option B (Neon table):

```sql
CREATE TABLE agent_handoffs (
  id BIGSERIAL PRIMARY KEY,
  from_agent TEXT NOT NULL,            -- 'Content Decay Detector'
  to_agent TEXT NOT NULL,              -- 'Content Producer' (canonical, not "Owl")
  kind TEXT NOT NULL,                  -- 'refresh_url', 'new_keyword', etc.
  payload JSONB NOT NULL,              -- validated by zod schema per (kind)
  source_ticket_id TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  consumed_at TIMESTAMPTZ,             -- null = pending
  consumed_by_execution_id TEXT,       -- the n8n execution that grabbed it
  result TEXT,                         -- 'drafted_ticket=X', 'voice_rejected', 'skipped'
  UNIQUE (source_ticket_id, kind)      -- ticket can only hand off once
);
CREATE INDEX idx_handoffs_pending ON agent_handoffs (to_agent, created_at) WHERE consumed_at IS NULL;
```

Plus REST endpoints:
- `POST /api/agent-handoffs` (write, called by AE's Write Handoffs)
- `GET /api/agent-handoffs?to=Content Producer&pending=true` (read, called by Owl)
- `POST /api/agent-handoffs/:id/consume` (mark consumed)

Migration: AE keeps writing the legacy `HANDOFF:{json}` marker AND inserting into the new table for 30 days. After 30d of zero legacy reads, remove the marker code.
