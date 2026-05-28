# Audit 01 — Active Agent Map

**Phase 1, read-only. Source of record: live n8n workflow JSON (pulled to
audit/data/workflows/) + execution history (pulled to audit/data/executions/).**

## Summary

| Agent | Workflow ID | Cron | Last 30 runs | Status |
|---|---|---|---|---|
| 🐢 Turtle | yYyj4TnP9Ho9O85l | Weekly Mon 7AM ET | 4 success / 2 error / 0 crashed (n=6) | DEGRADED — old errors stale |
| 🦉 Owl | d7YwC4ezub4g1LrI | Tue–Fri 9/13/17 ET | 12 success / 18 error / 0 crashed (n=30) | **🔴 40% SUCCESS — P0** |
| 🐝 Bee | N03TEmB50zG0XiiP | Daily 6AM ET | 25 success / 4 error / 1 crashed (n=30) | OK (83%) |
| 🦅 Eagle v3 | alik1C8sXr857rY7 | Mon 7AM ET | 21 success / 4 error / 0 crashed (n=25) | OK (84%) |
| ⚙️ Approval Executor | hpgbBAmRmqtsfr6g | Every 5 min | 30/30 success | ✅ 100% |

## Top current bugs (by agent, from live error data)

### 🦉 Owl — P0
**Root cause:** Top 2 errors are the same credential-type mismatch:
```
Fetch Refresh Queue: Credential with ID "S7YZNtmkHgtF72gN" does not exist for type "hubspotApi".
```
That credential ID is the HubSpot Private App but it's registered as type `httpHeaderAuth`, not `hubspotApi`. The Fetch Refresh Queue node was wired with the wrong credential type, so EVERY Owl run fails at that step. Owl proceeds with empty queue (continueOnFail likely on) but loses the cross-agent handoff feature entirely.

Older errors (3-5 days ago) are Anthropic API timeouts at Generate Draft + LinkedIn — likely transient.

**Receipt:** `audit/data/executions/owl-list.json` shows status breakdown; pulled error detail via `n8n /executions/{id}?includeData=true`. Latest error 2026-05-27T20:38:41.

### 🐝 Bee
Last 5 errors are all **from yesterday's iteration** (May 27) trying to detect redirects via various n8n-internal approaches (httpRequest helper, fetch, require('https')) before landing on `/api/check-redirect` Vercel proxy. These are stale — no longer happening since `/api/check-redirect` is the current path.

One real concern: `GSC 90d Search Analytics: Forbidden` (2026-05-27T18:40:38). Could indicate Google Search Console OAuth token expired. Needs verification but only happened once in the window.

### 🦅 Eagle
Two real bugs:
1. **`Fetch Rendered HTML: URL parameter must be a string, got undefined`** (2026-05-27) — classic `$json` clobbering pattern. Fetch Rendered HTML reads `$json.fetchedUrl` but upstream HTTP node response overwrote `$json`. **Same family as the AE id=0 bug.**
2. **`Preserve Metadata: Cannot assign to read only property 'name' of object 'Error'`** (2026-05-24) — code node tries to mutate an Error object. Defensive bug.
3. **`Structural Defect Scanner: Invalid or unexpected token`** (2 occurrences, 2026-05-24) — JS syntax error in scanner code.

Plus 1 silent skip from the Fetch Rendered HTML failure cascading.

### 🐢 Turtle
2 errors from 2026-05-22 only — `Create Decay Ticket: Bad request`. Stale, no recent recurrence.

### ⚙️ Approval Executor
30/30 success — perfect. The integrity check added yesterday (Mark Tickets Executed v4 with `branchHadError`) is doing its job, AND no upstream agents have been filing tickets AE can't process.

## What's NOT in this map

- **HANDOFF marker scan** → see audit/03-handoff-audit.md
- **Circular logic patterns across all workflows** → see audit/04-circular-logic-scan.md
- **Stage transition anomalies** → see audit/06-stage-transition-anomalies.md
- **Helper endpoint reliability** → see audit/05-vercel-helper-health.md

## Active-but-not-marketing workflows (infra, all healthy)

- `NAjdXCnFDyV5Z4Ep` Webhook: Process Decision (write) — UI hits this on approve/reject
- `VY38KRldIFDdvKJj` Webhook: Agent Proposals (read) — UI hits this for ticket lists. **Bug: bucket=done filter returns same as bucket=pending. See audit/06.**
- `Oq07XLN678zlAIaF` Webhook: Decision Memory — GET-only lookup
- `liOYeGcSZD8UQEOX` Heartbeat Aggregator — every 5 min, pulls last 20 executions, posts to heartbeat-store
- `P3fKgDjcHFIBwHgI` Agent Heartbeat Store — webhook receiver
- `P4qZX27Up0Hs9rcE` Agent Feedback Store — webhook receiver
- `ljEP1fIxSr2UmJHA` Master Kill Switch — 12 nodes, 18 edges. **Untested whether it actually halts in-flight executions vs only future ones.** This is a Section 6 violation if it doesn't halt in-flight.
- `P7c1fwvrmRV5CTrd` CF7 Form → HubSpot — not an agent, just a webform receiver
