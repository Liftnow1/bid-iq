# Audit 02 — Dependency Graph

```mermaid
graph TB
    %% Cron triggers
    cronT[Cron: Weekly Mon 7AM] --> Turtle
    cronO[Cron: Tue-Fri 9/13/17 ET] --> Owl
    cronB[Cron: Daily 6AM ET] --> Bee
    cronE[Cron: Mon 7AM ET] --> Eagle
    cronAE[Cron: Every 5 min] --> AE

    %% Data sources
    GSC[(Google Search Console)] --> Turtle
    GSC --> Bee
    WP_REST[(WordPress REST<br/>posts/pages/product_cat)] --> Bee
    WP_REST --> Owl
    WP_REST --> Eagle
    KB_API[Vercel /api/known-models<br/>KB-grounded products] --> Bee
    REDIR_API[Vercel /api/check-redirect<br/>HTTP 301 detection] --> Bee
    KB_ASK[Vercel /api/ask<br/>Claude + KB context] --> Owl
    ANTHRO[(Anthropic API)] --> Owl
    ANTHRO --> Bee
    ANTHRO --> Eagle
    HS_READ[(HubSpot tickets read<br/>via webhook agent-proposals)] --> Owl
    HS_READ --> Bee
    HS_READ --> AE

    %% Agent → ticket
    Turtle -->|files Refresh ticket| HS_TICKET[(HubSpot Pipeline 0<br/>Ticket)]
    Owl -->|files Content Draft ticket| HS_TICKET
    Bee -->|files SEO ticket| HS_TICKET
    Eagle -->|auto-patches WP<br/>+ files Summary ticket| HS_TICKET
    Eagle -->|direct PATCH| WP_WRITE

    %% Dashboard + approval
    HS_TICKET --> UI[bid-iq-neon.vercel.app<br/>/approvals dashboard]
    UI --> ProcDecision[Webhook: Process Decision]
    ProcDecision -->|stage 1→2 or 1→4| HS_TICKET

    %% AE chain
    HS_TICKET -->|fetches stage=2| AE
    AE --> ProcRoute[Process & Route]
    ProcRoute --> WriteHandoffs[Write Handoffs<br/>HANDOFF:json marker]
    WriteHandoffs --> RouteByAction[Switch: Route by Action]
    RouteByAction -->|wp_create| PubWP[Publish to WordPress]
    RouteByAction -->|wp_patch| PatchWP[WP PATCH Page]
    RouteByAction -->|email_send| SendEmail[Send Email SMTP]
    RouteByAction -->|linkedin_post| LinkedInBranch[Get LinkedIn Profile<br/>→ Post to LinkedIn<br/>DISABLED]
    RouteByAction -->|fallback auto_ack| MergeBranches
    PatchWP -.->|if product_cat| PatchYoast[PATCH Yoast Term Meta<br/>via /liftnow/v1 mu-plugin]
    PubWP --> MergeBranches[Merge All Branches]
    PatchYoast --> MergeBranches
    SendEmail --> MergeBranches
    LinkedInBranch --> MergeBranches
    MergeBranches --> MarkExec[Mark Tickets Executed<br/>integrity check]
    MarkExec --> PatchStage[PATCH Ticket Stage<br/>Auto-Applied 1363043699]
    PatchStage --> HS_TICKET

    %% Executor writes
    PubWP --> WP_WRITE[(WordPress write)]
    PatchWP --> WP_WRITE
    PatchYoast --> WP_WRITE

    %% Cross-agent handoffs
    Turtle -.HANDOFF: refresh_url.-> WriteHandoffs
    Owl -.reads stage 3<br/>HANDOFF tickets.-> HS_TICKET

    %% Heartbeats
    Turtle -.heartbeat.-> HB_STORE[Agent Heartbeat Store]
    Owl -.heartbeat.-> HB_STORE
    Bee -.heartbeat.-> HB_STORE
    Eagle -.heartbeat.-> HB_STORE
    AE -.heartbeat.-> HB_STORE
    HB_AGG[Heartbeat Aggregator<br/>every 5 min] --> HB_STORE

    classDef on fill:#d1fadf,stroke:#039855,color:#054f31;
    classDef helper fill:#dbeafe,stroke:#2563eb,color:#1e3a8a;
    classDef sink fill:#fef3c7,stroke:#d97706,color:#7c2d12;
    classDef warn fill:#fee2e2,stroke:#dc2626,color:#7f1d1d;

    class Turtle,Owl,Bee,Eagle,AE on;
    class KB_API,REDIR_API,KB_ASK,ProcDecision,HS_READ helper;
    class WP_WRITE,HS_TICKET sink;
    class LinkedInBranch warn;
```

## Key dependencies & failure modes

| Edge | What flows | What fails if broken |
|---|---|---|
| Bee → /api/known-models | Real model list | Bee hallucinates models, QA gate catches but no ticket filed |
| Bee → /api/check-redirect | Redirect destination | Bee patches redirect-zombie posts (the previous bug) |
| Owl → HubSpot (Fetch Refresh Queue) | Turtle handoffs | Owl can't see Turtle's refresh requests. **CURRENTLY BROKEN — credential type mismatch.** |
| AE → WP PATCH Page | Title/meta/content writes | Tickets stuck in stage 2 forever (the id=0 bug, fixed) |
| AE → PATCH Yoast Term Meta | Yoast SEO fields | Category titles don't update visibly (Yoast template fallback hides it partially) |
| Mark Tickets Executed → integrity check | rest_* error detection | Tickets falsely Auto-Applied when WP rejects |

## Cycle / race risks

| Risk | Where | Current status |
|---|---|---|
| Bee + Eagle race on same page | If both fire same day and target same URL | UNGUARDED — Section 7 calls out `wp_resource_locks` table as P2 |
| Owl picks same Turtle handoff twice | Owl's refresh queue de-dupe | Uses staticData processedHandoffs (50-item cap) — fragile |
| AE re-processes already-Auto-Applied ticket | If integrity check fails to set stage | Possible — Auto-Applied stage `1363043699` filter on Fetch Approved Tickets ensures stage 2 only, so closed tickets aren't re-fetched |
| Buck-pass loop (A enriches → B rejects → A retries) | None currently | Owl Self-Reject doesn't re-queue; it just logs. Safe. |

## Vercel helpers are single points of failure for Bee

`/api/check-redirect` and `/api/known-models` are **uniquely necessary** for Bee:
- Without `/api/check-redirect`: Bee patches redirect-zombie posts (old bug pattern)
- Without `/api/known-models`: Bee hallucinates models (yesterday's bug)

Currently no active monitoring on either. Section 4 P1 calls this out.
