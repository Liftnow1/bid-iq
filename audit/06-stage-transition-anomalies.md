# Audit 06 — Stage Transition Anomalies

**Pulled from:** HubSpot tickets pipeline 0, last 14 days, via MCP search and
agent-proposals webhook.

## Current state of ticket queue

### Pending tickets (stage 1 — Pending Review): 26 total

Distribution by agent:
| Agent | Count | Status |
|---|---|---|
| SEO Optimizer (Bee) | 8 | Current work, expected |
| Coordinator | 6 | 🟡 Coordinator is DEACTIVATED — these are zombies |
| UI/UX Performance (Eagle) | 5 | Current work, expected |
| SEM Manager (Tally) | 4 | 🟡 Tally is DEACTIVATED — zombies |
| Content Decay Detector (Turtle) | 2 | Expected |
| Backlink Builder (Spider) | 1 | 🟡 Spider is DEACTIVATED — zombie |

**Age distribution:**
- < 1h: 1
- 1-24h: 24
- 1-7d: 1
- 7-30d: 0
- >30d: 0

No tickets are pathologically stale. The 1-7d ticket is likely a stale Bee or Eagle that user hasn't reviewed yet — fine.

### Auto-Applied tickets (stage 1363043699 — chain succeeded): 18 in last 7 days

All from the 4 active agents (Turtle, Bee, Eagle, plus 1 old Backlink Builder from May 22 when Spider was last on). Confirms AE chain has been working since the id=0 fix yesterday.

## Anomalies found

### 🟡 Zombie tickets from deactivated agents

11 tickets currently in stage 1 from agents that are OFF:
- 6 Coordinator
- 4 SEM Manager
- 1 Backlink Builder

These will sit in Paul's inbox forever — no agent will execute them, and approving them does nothing because there's no AE branch handling those `action_type` values.

**Recommended cleanup (P1):** bulk-close these to stage 4 (Rejected) with outcome_notes `ZOMBIE — agent deactivated, ticket has no executor.` Filter approvals dashboard so zombies don't surface to Paul.

### 🔴 BUG: `agent-proposals?bucket=done` filter is broken

```bash
$ curl "https://agents.liftnowdirect.com/webhook/agent-proposals?bucket=pending&limit=100"
# returns 26 tickets, all stage=1

$ curl "https://agents.liftnowdirect.com/webhook/agent-proposals?bucket=done&limit=100"
# returns same 26 tickets, ALL still stage=1 — bucket filter ignored
```

The `bucket=done` param should filter to stage=3 (Deferred) or stage=1363043699 (Auto-Applied) or stage=4 (Rejected) depending on semantics. Currently it returns all pending. **The approvals dashboard shows "done" tickets that are actually still pending.**

Fix lives in n8n workflow `VY38KRldIFDdvKJj` Webhook: Agent Proposals (read). Need to inspect its filter logic and add proper `bucket` -> stage mapping.

### 🟢 No tickets stuck at stage 2

AE polls every 5 min. If anything gets approved, it should be picked up within 5 min and either Auto-Applied or rolled back to stage 1. Currently zero tickets in stage 2 — clean.

### 🟢 No oscillation between stages

Searched for tickets with >3 stage transitions in last 14d. None found.

## Stage ID reference (for the typed constants module)

| Stage label | hs_pipeline_stage value | Used by |
|---|---|---|
| Pending Review | `1` | UI shows "Decide" tab |
| Approved | `2` | AE picks these up via Fetch Approved Tickets |
| Deferred | `3` | Was used incorrectly yesterday as "Done"; do NOT use |
| Rejected | `4` | User rejection via UI |
| Auto-Applied | `1363043699` | AE writes this after successful execution |

**P2 item:** wrap these in `app/lib/hubspot-stages.ts` so Code nodes + Vercel APIs reference them by name, not magic number.

## Stage transition log opportunity

There's no log of who/what moved a ticket between stages. HubSpot has built-in `hs_pipeline_stage_modification_history` audit but we don't surface it. Future P2: build a `ticket_transitions` view that joins ticket activity log with the original n8n execution ID that drove the transition.
