# Audit 09 — Triaged Fix Plan

**Phase 2 output.** Default order is the prompt's pre-approved priority,
adjusted for the new bugs discovered in Phase 1.

---

## Pre-empt: 3 urgent items NOT in the prompt's default but found in audit

These are bugs I found in Phase 1 that the prompt didn't anticipate. They go FIRST.

### URGENT-1: 🦉 Owl `Fetch Refresh Queue` credential type mismatch
**Symptom:** Owl fails every run at the queue-fetch step with `Credential "S7YZNtmkHgtF72gN" does not exist for type "hubspotApi"`. Causes 40% of recent failures.
**Fix:** Change node's `nodeCredentialType` from `hubspotApi` to `httpHeaderAuth`. 5-minute n8n PUT.
**Receipt-after-fix:** fire Owl manually, paste execution ID showing Fetch Refresh Queue completes successfully.

### URGENT-2: 🦅 Eagle `Fetch Rendered HTML` reads clobbered $json.fetchedUrl
**Symptom:** "URL parameter must be a string, got undefined" — same id=0 family bug.
**Fix:** Change URL expression to read from the upstream named Code node (Get Page Inventory or similar) via `$('NodeName').itemMatching($itemIndex).json.fetchedUrl`.
**Receipt-after-fix:** fire Eagle manually, paste execution ID showing Fetch Rendered HTML resolves to a real URL.

### URGENT-3: 🔴 `bucket=done` filter broken in agent-proposals webhook
**Symptom:** Dashboard's "done" view shows the same tickets as "pending". Users can't tell which work is finished.
**Fix:** In workflow `VY38KRldIFDdvKJj` Webhook: Agent Proposals (read), inspect the Code/IF node that routes by `bucket` query param. Likely doesn't map "done" → stage 1363043699.
**Receipt-after-fix:** curl `/webhook/agent-proposals?bucket=done` and confirm only stage=1363043699 (or 3 or 4 depending on chosen semantics) tickets returned.

These three are NOT optional. URGENT-1 in particular is causing live Owl runs to half-fail right now.

---

## P0 (Wave 1, per prompt)

### P0-A: Light up the watchdogs

Re-activate Silent Failure Detector (`eUhp1uc2wj4SrDbQ`) and Agent Failure Alerts (`qMeBXIjguVuaKLLF`) via the 5-step gate.

Before flipping active:
- Read the workflow JSONs
- Confirm credential types (likely have the same hubspotApi vs httpHeaderAuth bug as Owl)
- Trigger a forced-failure on a sacrificial test ticket, watch alert fire
- Confirm Agent Failure Alerts emails paulj@liftnow.com

**Receipt:** test ticket ID, n8n execution IDs for both, screenshot of email Paul received (or SMTP log proof).

### P0-B: Promote HANDOFF marker to a real contract

Per prompt Section 4 P0 item #2, Option B (Neon table).

Migration steps:
1. **Add table** in `lib/db.ts` schema bootstrap:
   ```sql
   CREATE TABLE agent_handoffs (
     id BIGSERIAL PRIMARY KEY,
     from_agent TEXT NOT NULL,
     to_agent TEXT NOT NULL,
     kind TEXT NOT NULL,
     payload JSONB NOT NULL,
     source_ticket_id TEXT NOT NULL,
     created_at TIMESTAMPTZ DEFAULT NOW(),
     consumed_at TIMESTAMPTZ,
     consumed_by_execution_id TEXT,
     result TEXT,
     UNIQUE (source_ticket_id, kind)
   );
   CREATE INDEX idx_handoffs_pending ON agent_handoffs (to_agent, created_at) WHERE consumed_at IS NULL;
   ```
2. **Add REST endpoints**:
   - `POST /api/agent-handoffs` — write new handoff
   - `GET /api/agent-handoffs?to=Owl&pending=true` — read queue
   - `POST /api/agent-handoffs/:id/consume` — mark consumed
3. **Update AE's Write Handoffs Code node** to ALSO POST to /api/agent-handoffs (keep legacy outcome_notes marker for 30d dual-write)
4. **Update Owl's Fetch Refresh Queue** to call /api/agent-handoffs instead of HubSpot search
5. **30 days later**: remove HANDOFF: marker writes

**Receipt:** SQL `SELECT count(*) FROM agent_handoffs` shows rows after AE runs; Owl fires and consumes one; consumed_at + consumed_by_execution_id populated.

---

## P1 (Wave 2)

### P1-A: Minimal Team Manager
Daily cron. Reports per-agent ticket counts (filed/approved/auto-applied/rejected/orphaned-handoffs). Emails paulj@liftnow.com.

Implementation: new n8n workflow that queries HubSpot tickets via search + agent_handoffs table via REST, builds a markdown summary, emails via SMTP node.

**Receipt:** sample report sent to paulj@liftnow.com on a manual fire; screenshot or SMTP log.

### P1-B: Bound Eagle's auto-patch authority
- Max 25 auto-patches per run (already informally true per audit; codify)
- Max 3 per page (deduplicate by URL)
- Daily Eagle digest ticket so Paul sees what it did
- Forced-failure test triggers rollback

**Receipt:** n8n execution showing the 26th candidate skipped; HubSpot ticket showing the digest.

### P1-C: Vercel Helpers Watchdog
New n8n workflow `Vercel Helpers Watchdog`. Cron 15min. Pings both endpoints with a known input. On failure, files a P0 HubSpot ticket AND triggers Master Kill Switch on Bee.

**Receipt:** workflow exists in n8n; cron history shows 4 runs/hour; intentional /api/known-models break (e.g. invalid brand) triggers the failure path; alert ticket appears.

### P1-D: Owl Self-Reject feedback loop
- Neon table `self_reject_log` (ticket_attempt_id, draft_preview, failed_checks JSONB, created_at)
- Owl's Self-Reject Gate writes a row instead of just logging to staticData
- Weekly aggregate view: top failed checks

**Receipt:** SQL query of self_reject_log showing entries after Owl runs; aggregate view shows top 3 failed checks.

### P1-E: Hard-fail Bee on Vercel helper outage (per audit/05)
Bee currently silently degrades if `/api/check-redirect` or `/api/known-models` fails. Change to: set `_inItem.skipped=true` and bail. Don't draft on uncertain data.

**Receipt:** force-fail one of the endpoints (e.g. wrong URL), fire Bee, confirm it files no ticket and logs the skip reason.

### P1-F: Bulk-close 11 zombie tickets (per audit/06)
Tickets from Coordinator (6), SEM Manager (4), Backlink Builder (1) that won't execute. Bulk move to stage 4 with outcome_notes "ZOMBIE — agent deactivated, no executor."

**Receipt:** HubSpot search shows 0 stage=1 tickets from deactivated agents after the cleanup.

### P1-G: Fix /approvals UX issues (per audit/08)
- Header agent count corrected to "4 agents active · 23 paused"
- Loading… replaced with plain-English error after 15s
- Diff view on Bee approval cards (current vs proposed)
- Zombie ticket filter

**Receipt:** before/after screenshots; live testing.

---

## P2 (Wave 3)

### P2-A: `app/lib/hubspot-stages.ts` typed constants
Wrap pipeline stages. Refactor every reference in code and n8n Code nodes.

### P2-B: Zod schema-validate `recommendation_detail`
Per recommendation_type. Validate on agent write side AND AE read side.

### P2-C: AE Switch → registry pattern
Neon table `agent_action_registry` (action_type, executor_branch_name, owner_agent, integrity_check_fn). Switch reads dynamically.

### P2-D: WP write-lock (`wp_resource_locks` table)
Defer concurrent PATCHes by 30s + retry.

### P2-E: Yoast mu-plugin v3 full verification
Carry-over from yesterday. Fire Bee on a fresh product_cat ticket, approve, confirm `yoast_head_json.title` AND `.description` change on live page after the indexable-rebuild logic in the updated plugin.

---

## P3 — The 23-agent re-activation slow burn

Per audit/07's wave list. Two weeks per agent at minimum after watchdogs are mature (per Section 8 final note).

Suggested order is documented in `audit/07-23-deactivated-inventory.md`.

---

## Execution sequence for this session

If I have ~8 hours:

| Block | Time | Goal |
|---|---|---|
| Block 1 (~60min) | 90-150min mark | URGENT-1, URGENT-2, URGENT-3. Each gets the Section 5 loop. Receipt per. |
| Block 2 (~90min) | 150-240min mark | P0-A: re-activate Silent Failure Detector + Agent Failure Alerts via 5-gate. |
| Block 3 (~120min) | 240-360min mark | P0-B: agent_handoffs table + REST endpoints + AE dual-write + Owl new read path. |
| Block 4 (~60min) | 360-420min mark | P1-F (zombie cleanup) + P1-C (helpers watchdog) — both quick. |
| Block 5 (~90min) | 420-510min mark | P2-A (typed stages) + P2-E (Yoast verification end-to-end). |
| Block 6 (buffer) | 510-end | P1-A (Team Manager) or P1-G (UX) depending on time remaining. |

End-of-shift report at 99-end-of-shift.md regardless of where I stop.

## What I will NOT touch this session

- ❌ Re-activating any of the 23 deactivated agents beyond P0-A (Silent Failure Detector, Agent Failure Alerts)
- ❌ Maverick / Tally (Section 7 forbidden — no real ad spend tests)
- ❌ Spider (real email send) — outreach gates not yet wired
- ❌ Bumping cron schedules
- ❌ WP-Admin user changes
- ❌ HubSpot pipeline changes (only ticket properties)
- ❌ Anything that requires SFTP / SSH / new admin credentials

These are gates I'm leaving for Paul to clear.
