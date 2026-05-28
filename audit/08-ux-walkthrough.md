# Audit 08 — UX Walkthrough of /approvals

**Source:** Live fetch of `https://bid-iq-neon.vercel.app/approvals` during Phase 1.

## What's on the page right now

### Header
- "Liftnow Agent Approvals"
- "10-agent marketing team · connecting…"
  - ⚠️ INACCURATE: the platform has 4 active marketing agents (Turtle, Owl, Bee, Eagle), not 10. The "10-agent" string is stale from earlier in the build.

### Tabs
- 🏠 Today
- ✋ Decide
- 📊 Progress

### Content area
- Persistent `Loading…` state when WebFetch hit the page
- The page is a client-side SPA that fetches from `/webhook/agent-proposals?bucket=pending` and `bucket=done` on load
- For a brand-new browser session, the user sees `Loading…` until the n8n webhook responds (~2-3s normally)

## Issues to fix (per Section 4 Law 4 — Operator-First UX)

### P1 — Header lies about agent count
"10-agent marketing team · connecting…" doesn't match reality. Should be:
- "4 agents active · 23 paused" (counts pulled dynamically from /webhook/agent-proposals or a new /api/agent-status endpoint)
- OR drop the count and say "Marketing agent team"

### P1 — `bucket=done` filter is broken (cross-reference audit/06)
The "Done" tab in the dashboard (or wherever bucket=done is used) shows the SAME tickets as Pending because the n8n webhook ignores the bucket param. Cosmetic until fixed.

### P1 — `Loading…` has no plain-English failure mode
If `/webhook/agent-proposals` is down or slow, the user sees `Loading…` forever. Need:
- Timeout after 15s
- Plain-English error: "Can't reach the agent team backend. The n8n service at agents.liftnowdirect.com may be down. Try the Refresh button or check back in 5 min."

### P1 — No diff view on approval cards
The approval flow currently shows the FULL ticket body (with ASK / WHY / IF YOU APPROVE / IF YOU REJECT framing). For Bee's SEO patches specifically, Paul sees the proposed new title/meta/section but NOT the current ones. He has to mentally diff. Should show:

```
TITLE
- Current: "Vehicle Lifts"
- Proposed: "Car Lifts for Garage & Fleet Bays | Commercial Vehicle Lifts"

META DESCRIPTION
- Current: (empty)
- Proposed: "Government-contract car lifts for municipal garages..."
```

This requires the approval card to fetch the live WP REST resource and render side-by-side. ~30-60 min UI work.

### P2 — Tabs need text labels (per WebFetch agent's note)
Emoji-only labels are guessable but not accessible. Suggest:
- 🏠 Today (keep as-is — works)
- ✋ Decide → "✋ Decide" with explicit subtext "Tickets needing your call"
- 📊 Progress → "📊 Progress" with explicit subtext "Live changes + ROI"

### P2 — Activity feed shows JSON-ish details
The Today tab's outcome rows are decent (icon + headline + detail + link) but a few patterns leak technical jargon:
- "Eagle scan complete — 7 content fixes need your review" ← good
- "WP page 9044 updated by Bee (PAGE_2_STRUGGLE). Changed: title, meta, new section." ← exposes opportunity code; could be "Bee updated the Vehicle Lifts page (was stuck on Google page 2). Changed title, meta description, and added a new section."

### P3 — Zombie ticket suppression
Per audit/06, 11 tickets are from deactivated agents (Coordinator, SEM Manager, Backlink Builder). Filter these out of pending unless `agent_name` matches a currently-active agent.

## What works well (don't break)

- The 3-tab rewrite (Today / Decide / Progress) is much cleaner than the old 8-tab layout
- Action buttons (Approve / Reject) are clear and color-coded
- Today's outcome feed groups by day, badges live/draft/auto/needs items correctly
- Refresh button exists for explicit re-fetch

## Recommended Operator-First fixes (P1 bundle, ~2 hours of work)

1. Fix the agent count display ("4 agents active · 23 paused")
2. Fix bucket=done filter (in n8n workflow)
3. Add 15s timeout + plain-English error on `Loading…`
4. Add diff view to Bee approval cards (current vs proposed)
5. Filter zombie tickets from pending bucket

If the prompt's "approvals dashboard must show diff — not raw JSON" is taken literally, #4 is the highest-leverage UI fix.
