# Audit 07 — Deactivated Agent Inventory (the 23 OFF)

**Method:** Listed all workflows from n8n API. Cross-referenced with the
deactivated list in AGENT_TEAM_STATUS.md. For each, blockers to re-activation
through the Section 8 five-gate process.

**Currently active in n8n: 13 workflows (5 marketing + 8 infra).**
**Currently inactive (the 23): everything else marketing-related.**

## Re-activation order (per prompt Section 4 P3)

Listed in the order Paul pre-approved. Each row maps to one cycle through
the 5-step gate in Section 8.

### Wave 1 — Watchdogs (P0 in the prompt's plan)

| Order | Agent | Workflow ID | Notes / blockers |
|---|---|---|---|
| 1 | Silent Failure Detector | `eUhp1uc2wj4SrDbQ` | Lightweight — fetches recent executions, flags repeated failures. Re-activate first. Need to confirm its HubSpot ticket-creation node uses correct credential type (httpHeaderAuth not hubspotApi — same bug as Owl). |
| 2 | Agent Failure Alerts | `qMeBXIjguVuaKLLF` | Webhook receiver. Sub-agent that reacts to Silent Failure Detector tickets. Verify webhook is reachable + downstream email node has paulj@liftnow.com. |

### Wave 2 — Visibility (P1)

| Order | Agent | Workflow ID | Notes / blockers |
|---|---|---|---|
| 3 | Team Manager (Weekly Self-Improvement) | `wTkLhqNY7pfIU5ro` | Per prompt: daily cron, reports tickets filed/approved/handed-off, agents that didn't fire. Likely needs partial rewrite to match new structure. Hold until Wave 1 stable. |
| 4 | Daily ROI Tracker | `JjIHLI2AdZ3DfnbY` | Reads Google Ads + Microsoft Ads, computes spend/conversion deltas. Pre-req for Maverick/Tally re-activation. Confirm OAuth tokens still valid. |
| 5 | Hound (Keyword Discovery) | `4FoOz5TX184zH0Zn` | Monthly cron. Feeds Bee + Owl. Action: `auto_ack` (just files a ticket; Paul reviews). No AE branch needed beyond fallback. Inspect for $json.X clobbering. |
| 6 | Pattern Detector | `ixerSw7gbu26M1DP` | Weekly themes from Decision Memory. Read-only. Low risk. |
| 7 | Ticket Triage Digest | `i8AjYKBt0V15VGA7` | Triages stage-1 backlog. Read-only on HubSpot. Verify credential. |

### Wave 3 — Sentinels

| Order | Agent | Workflow ID | Notes / blockers |
|---|---|---|---|
| 8 | Duplicate Workflow Detector | `CsPth7e5pqRdBDln` | Scans n8n for accidental duplicates. Read-only. |
| 9 | Content Dup Sentinel | `Zufhtq3fu4AJR6an` | Already audited yesterday — has $json.ticketId reference but upstream is a Code node so it's actually safe. Re-activate. |
| 10 | Asset Review Gate | `f25BTprTci297v7W` | Sub-agent. Reviews proposed visual assets before Owl publishes. Re-activate after Owl is stable. |

### Wave 4 — Outreach (extra-strict gates)

| Order | Agent | Workflow ID | Notes / blockers |
|---|---|---|---|
| 11 | Beacon (LinkedIn Cadence v2) | `f7d4k95CQPJMiuqv` | Currently has `Auto-Post LinkedIn` node disabled. Need to verify draft-and-paste path works (file ticket with LinkedIn draft body, Paul copies to LinkedIn manually). |
| 12 | Crow (Reddit Submissions) | `VdYI5nLqDTt5jWYR` | Manual-paste only. Reddit closed self-service API in 2026. Verify it files clean draft tickets. |
| 13 | Reed (Community Engagement / Bat-Reply) | `QrKVBx6jtkseu8xH` | Same model — manual paste. Verify thread parsing is robust to Brave Search API response shape. |

### Wave 5 — Read-only intel

| Order | Agent | Workflow ID | Notes / blockers |
|---|---|---|---|
| 14 | Cat (Competitor SERP Monitor v2) | `dEKZkETwoVsSQO0A` | Ahrefs SERP queries. Rate-limited. Read-only. |
| 15 | Nox (Brand Listening) | `uRIzR7Uh8d9fUx9T` | GNews + HN multi-source. Read-only. |
| 16 | Site Audit Monitor | `QBaTurTGBiAo113f` | Crawls liftnow.com for broken links / 404s. Read-only. |
| 17 | SEO Keyword Rank Tracker | `BDRaqnlzKM2lv3iA` | Ahrefs rank tracking. Read-only. |
| 18 | Coordinator (Weekly Summary) | `xZCWfSlfZEZYXuOV` | Daily Briefing's weekly sibling. Confirm not duplicate of Team Manager (wave 2 item 3). |
| 19 | Daily Briefing | `97cM8tRymJ06WEEL` | Morning digest. Read-only. |
| 20 | Coordinator (Daily Briefing) | `VtBvtrYqS4vChTi2` | **POTENTIAL DUPLICATE of #19.** Investigate. One should probably be deleted. |

### Wave 6 — Highest-risk write paths (LAST)

| Order | Agent | Workflow ID | Notes / blockers |
|---|---|---|---|
| 21 | Maverick (SEM Auto) | `rZW6J0ccvzWjDxYr` | Writes to Google Ads. Section 7 hardening required: hard daily spend cap, dry-run default, sem_change_log table. |
| 22 | Tally (SEM Manager) | `tW1hYtfGWddfg7TF` | Reads ads + drafts changes. Less risky than Maverick but still production-impact. |
| 23 | Spider (Backlink Builder) | `YP07sPEQgTyrQ5BK` | Sends outreach emails to peer vendors. Section 7 hardening: paulj@liftnow.com gate, no real send without approval. |

## Common blockers (apply to multiple agents)

1. **Credential type mismatch** (Owl-class bug): inspect every HTTP node that auths to HubSpot. If `nodeCredentialType: 'hubspotApi'` but credential is `httpHeaderAuth`, fix BEFORE re-activating.
2. **$json clobbering** (Eagle Fetch Rendered HTML class bug): scan every Code/HTTP node sequence.
3. **action_type → AE branch coverage**: confirm Process & Route handles the agent's emitted action_type, or that the Switch fallback covers it as auto_ack.
4. **HubSpot ticket schema drift**: deactivated agents may emit `recommendation_detail` shapes that pre-date today's wiring. zod validation on AE read side would catch.
5. **Stale credentials**: GSC OAuth, LinkedIn OAuth, etc. may have expired.

## Acceptance criteria per agent (the 5-gate)

Before flipping any one of the 23 to active:

- [ ] Gate 1: Documented — workflow JSON read in full, action_type, ticket shape, cron schedule
- [ ] Gate 2: Wired — AE branch exists (or fallback covers), HTTP nodes have continueOnFail, no $json clobbering
- [ ] Gate 3: Integrity check — AE Mark Tickets Executed catches downstream errors; forced-failure rolls back
- [ ] Gate 4: Dry run — fire manually, real ticket, approve, AE picks up, downstream change verified
- [ ] Gate 5: Watchdog — agent appears in Heartbeat Aggregator; 7 days zero silent failures

Document completion per agent in this file (append rows below as each is brought back).

---

## Re-activation log (append as completed)

(Empty — Wave 1 not yet started)
