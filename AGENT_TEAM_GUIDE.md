# Liftnow Agent Team — Your Plain-English Operating Guide

Last updated: 2026-05-25
For: Paul (the boss)
Author: Claude

---

## TL;DR — the 30-second version

You have **13 marketing agents** running on your Hetzner server. They wake up on schedules, do work autonomously (write content, send emails, edit web pages, post to LinkedIn, monitor competitors), and file **receipts** of what they did into a web dashboard you read each morning.

You don't approve anything in advance. The agents already did the work. **Your job each morning is to:**
1. Read what they did
2. Comment on anything they got wrong (the agents read your comments and learn)
3. Hit the kill switch if anything goes off the rails

Total time: ~10–20 minutes a day. The agents work 24/7.

---

## The Mental Model — important to internalize

You're not a "user approving requests." You're a **boss reviewing your team's daily output.**

A real marketing team works like this: you hire people, give them context, they work autonomously, and you check in periodically to redirect them. You don't pre-approve every email they send. You read what they sent yesterday and say "do more of X, stop doing Y."

**Same model here.** The agents are smart, follow your strategy, but will sometimes get it wrong. Your feedback in the form of comments is what makes them better over time.

The system has built-in safety:
- **Kill Switch**: one command stops everything if it goes haywire
- **Self-Reject Gates**: agents check their own work against your voice rules before publishing
- **Decision Memory**: agents skip proposing things you previously rejected
- **Team Manager (meta-agent)**: weekly review surfaces problems automatically

---

## Where to look

### Your dashboard (open this every morning)
**🔗 https://bid-iq-neon.vercel.app/approvals/index.html**

This is your "morning newspaper." Bookmark it. It's a static page hosted on Vercel — works on phone, tablet, desktop.

### Your inbox
**paulj@liftnow.com** — agents that send email send here

### WordPress admin
**https://liftnow.com/wp-admin** — when Content Producer publishes a PRIVATE draft, you'll find it here under Posts → Drafts

### The n8n server (only if something breaks)
**https://agents.liftnowdirect.com** — where the agents actually live. Login required. Don't go here unless you need to investigate.

---

## The Dashboard Tabs Explained

When you open the dashboard, you'll see 5 tabs at the top:

### 1️⃣ Activity (default — what agents DID)
The most important tab. Each card here represents one thing an agent **already did**. The agent isn't asking permission — it's showing you the receipt.

Each card has:
- An **agent badge** (color-coded, tells you which agent)
- A **subject line** (what was done — e.g. "Sent outreach to firehouse.com")
- A **priority pill** (LOW/MEDIUM/HIGH — how much you should care)
- **Age** (how long ago it happened)
- A **comment box** at the bottom
- A **File & Feedback** button

**What you do here**: read each card, comment if you want to teach the agent something, click File & Feedback.

### 2️⃣ Needs You (rare — proposals waiting for approval)
In the new autonomous mode, this should usually be empty. If something lands here, it means an agent intentionally paused work and wants your call. Approve/Reject/Defer/Approve with Conditions.

### 3️⃣ Inbox (informational stuff)
Daily Briefings, Pattern Detector themes, Coordinator notes, Team Manager weekly review. These are just FYIs. Read them, click **Acknowledge & Clear**.

### 4️⃣ All Done (history)
Everything that's been resolved. Searchable. Useful for "what did agent X do last week."

### 5️⃣ Learnings
Every comment you've ever left, by agent. This is how the agents "remember" what you've said.

---

## Your Morning Routine (literal steps)

Open the dashboard. Do this in order:

### Step 1 — Read the `[TRIAGE]` ticket first
Every day at 7:45 AM ET, the **Triage Digest agent** files a ticket called `[TRIAGE] Today's top N — {date}`. This is your hit-list. It picked the most important 5 things from everything pending. If you only have 5 minutes, just do these 5.

### Step 2 — Check your inbox
Two emails to expect at minimum:
- **`[Liftnow SEM] {ACTION}: {keyword}`** — from `partnerships@liftnow.com`, weekdays 6:30 AM. Tells you what to change in Google Ads. Either do it in Ads, or comment on the corresponding Activity card "ignore — bid is fine."
- **`[DRAFT FOR YOUR REVIEW] Outreach to {domain}`** — on Tuesdays only at 9 AM. The Backlink Builder wrote a guest-content pitch and emailed it to you. **You forward it** to the actual editor contact you want to reach. If the pitch is bad, comment in the Activity feed and the agent will adjust next Tuesday.

### Step 3 — Check WordPress drafts (Tuesdays)
**https://liftnow.com/wp-admin/edit.php?post_status=private**
Content Producer publishes one private draft per Tuesday. Read it. If it's good, change status to "Published." If it's mediocre, edit it then publish. If it's bad, delete and comment on the Activity card "wrong angle on this topic" or similar.

### Step 4 — Triage the Activity feed
Go through each card chronologically (newest first). For each:
- **Looks good** → just click File & Feedback (no comment needed). Agent did right.
- **Looks wrong** → type a comment in the box explaining why, then File & Feedback. The agent reads this on its next run and adjusts.
- **Don't care / acknowledge** → leave comment blank, click File & Feedback.

### Step 5 — Clear the Inbox tab
Daily Briefings, Coordinator notes, Pattern themes. Read, acknowledge, move on. These aren't decisions, just FYIs.

That's it. ~10–20 minutes/day.

---

## How to Comment Effectively

Comments are how you train the agents. **The system reads them.** Two ways they're used:

1. **Decision Memory** (within ~5 min): the next time any agent considers a similar proposal, it checks for recent rejections with similar signatures. If you commented "stop pitching APTA," it skips APTA.
2. **Weekly Pattern Detector** (Mondays 11 AM): summarizes themes in your comments and tells all agents "Paul consistently rejects X, prefers Y."

### Good comment patterns

| When you want to... | Write this |
|---|---|
| Stop a specific topic | "Don't propose this again — we've already covered it" |
| Adjust voice | "Use 'service bay' not 'depot bay'" |
| Block a target | "APTA isn't interested, stop pitching them" |
| Reframe strategy | "Focus on transit + school dual-vertical, not general fleet" |
| Praise (yes, do this) | "Yes, more like this. The honest-hedge framing is exactly right." |
| Calibrate priority | "This is high priority — push harder on Sourcewell angles" |

### Bad comment patterns (don't bother)

- "Nope" — too short, no signal
- "Try again" — doesn't tell the agent what to fix
- Sarcasm/humor — agents will take it literally

---

## Meet Your 13 Agents (memorize these names)

| Agent | What it does | When it runs | What you see |
|---|---|---|---|
| **SEM Manager** | Reads Google Ads, recommends bid/keyword changes | Weekdays 6:30 AM | Email + Activity card |
| **Content Producer** | Writes 2,800-word resource pieces, publishes WP private drafts | Tuesdays 9 AM | WP draft + Activity card |
| **Backlink Builder** | Drafts outreach to trade publications | Tuesdays 9 AM | Email to you + Activity card |
| **Backlink Poster** | Submits Liftnow to ~40 directories/day | Daily | Activity cards |
| **LinkedIn Cadence** | Posts on your personal LinkedIn (M/W/F) | M/W/F 10:30 AM | Real LinkedIn post + Activity card |
| **SEO Optimizer** | Edits page titles/meta on liftnow.com | Daily 6 AM | Live WP edits + Activity card |
| **UI/UX Performance** | Scans pages for missing brand elements | Mondays 7 AM | Activity card |
| **Content Decay Detector** | Compares GSC 28d windows, flags drops | Mondays 7 AM | Activity card |
| **Keyword Discovery** | Pulls Ahrefs competitor keywords | 1st of month 6 AM | Activity card (batch) |
| **Competitor SERP Monitor** | Tracks 30 keywords daily | Weekdays 7 AM | Inbox card when movement |
| **Brand Listening** | Monitors Reddit for mentions | Every 30 min | Inbox card if mention found |
| **Reddit Engagement** | Drafts Reddit replies | M/W/F 11 AM | Activity card |
| **Coordinator** | Cross-agent friction analysis | Daily 10 AM | Inbox card |
| **Daily Briefing** | Summary of yesterday's processed items | Daily 7:30 AM | Inbox card |
| **Pattern Detector** | Weekly themes in your decisions | Mondays 11 AM | Inbox card |
| **Triage Digest** | Picks top 5 pending tickets for the day | Daily 7:45 AM | `[TRIAGE]` card |
| **Team Manager (meta)** | Reviews the WHOLE TEAM's week, finds bugs/opportunities | Sundays 10 PM | `[META]` card in Inbox |

---

## How to Watch for Progress

### Quick health check (anytime)
The dashboard footer shows "updated 9:42 AM" — that's when it last refreshed from the server. If it's fresh, the system is alive.

### Did the agents actually do anything this morning?
1. Open dashboard
2. Click **Activity** tab
3. Look for cards dated today

If there are no cards from this morning at all, something's wrong. Check the kill switch wasn't activated, then look at n8n directly.

### Did my comments stick?
1. Click **Learnings** tab
2. You'll see your comments grouped by agent
3. The agents read this list on every run

### What's been auto-executed in the last week?
Click **All Done** tab. Filter or scroll. AUTO_EXECUTED items show what agents did without human approval — the autonomous work.

### Is the team improving over time?
Wait for Sunday 10 PM ET. The **Team Manager** files a `[META] Weekly Team Review` ticket with:
- Approve rate per agent (are you saying yes more over time?)
- Dead patterns (agents that produce nothing actionable)
- New opportunities (the meta-agent suggests new agents to build)
- Configuration drift (deprecated APIs, stale data, etc.)

---

## When Something Goes Wrong

### Spam — too many emails or duplicate cards
**Kill switch — easiest path: button in the dashboard.**

In the bottom-right of the Today tab there's a red "**Emergency: pause all agents**" button. Click it, confirm the popup, done. Pauses 8 agents.

**If the dashboard is down, use curl:**
```bash
curl -X POST https://agents.liftnowdirect.com/webhook/kill-switch \
  -H "Content-Type: application/json" \
  -H "x-agent-secret: LiftnowAgentTeam_kS9-bGq2_xVtN4mP" \
  -d '{"action":"deactivate"}'
```

The `x-agent-secret` header is required as of the security hardening pass. Save the secret somewhere accessible (password manager). It's also baked into the dashboard so the UI button just works.

This stops these 8 agents instantly: SEM, Backlink, Backlink Poster, LinkedIn, Content Producer, SEO, UI/UX, Content Decay.

Reactivate when ready:
```bash
curl -X POST https://agents.liftnowdirect.com/webhook/kill-switch \
  -H "Content-Type: application/json" \
  -H "x-agent-secret: LiftnowAgentTeam_kS9-bGq2_xVtN4mP" \
  -d '{"action":"activate"}'
```

### Embarrassing LinkedIn post
1. Go to your LinkedIn personal feed → delete the post manually
2. Kill switch LinkedIn specifically (or all):
   - Open https://agents.liftnowdirect.com
   - Find "Agent 3 — LinkedIn Cadence v2"
   - Toggle Active to Off
3. Comment on the corresponding Activity card explaining what was wrong

### Bad outreach email already sent
Backlink Builder routes drafts to YOU first — it doesn't actually send to external editors. So this can only happen if you forwarded a bad draft. Recover by:
1. Reply to the editor with a correction
2. Comment on the Activity card so the agent doesn't pitch them again
3. Add a personal note in your relationship CRM (HubSpot)

### Agent crashed / not running
1. Check n8n: https://agents.liftnowdirect.com → see if workflow shows last execution as ERROR
2. Click the workflow, look at the failed execution
3. Tell me (Claude) what the error message says — I'll fix it

---

## Common Situations — what to do

### "I see 47 cards in Activity and I'm overwhelmed"
The `[TRIAGE]` ticket picked the top 5. Read those. File-and-feedback the rest with no comment to clear the queue. Anything truly urgent will resurface in the next Triage Digest tomorrow.

### "An agent keeps proposing the same thing I've rejected 3 times"
That's a Decision Memory failure — its signature matching is too narrow. Tell me (Claude) which agent + which topic, and I'll tighten its signature.

### "I want to add a new agent"
Two options:
1. **Easy**: write down what you want it to do, when, and what its output should look like. Send to me. I build it.
2. **Harder**: the Team Manager (meta-agent) will surface "new opportunities" every Sunday — if it suggests one you like, comment "yes build this" on the meta-ticket.

### "I want the agent to be more aggressive / less aggressive"
Comment on a few Activity cards with phrases like:
- "Push harder on Sourcewell mentions next time"
- "Hedge more, this is too direct"
- "Stop being so conservative on bid raises"

After 3-5 such comments, Pattern Detector will notice and update agent prompts.

### "I'm going on vacation for a week — pause everything"
Kill switch:
```bash
curl -X POST https://agents.liftnowdirect.com/webhook/kill-switch \
  -d '{"action":"deactivate"}' -H "Content-Type: application/json"
```

When you get back:
```bash
curl -X POST https://agents.liftnowdirect.com/webhook/kill-switch \
  -d '{"action":"activate"}' -H "Content-Type: application/json"
```

---

## Day 1, Day 7, Day 30 — what to watch for

### Day 1 (tomorrow)
- Will the agents actually fire? (Expected: yes, all 7 cron schedules trigger)
- Will the Triage Digest at 7:45 AM be useful? (Expected: yes, picks top 5 from morning's output)
- Will the SEM Manager email at 6:30 AM be actionable? (Expected: yes — real Google Ads data drives it now)
- Will the Backlink draft be embarrassing? (Expected: no, drafts are good — but YOU review before forwarding so the risk is contained)

**Goal**: get through one day with no kill switch needed. Comment on at least 3 cards.

### Day 7 (next Monday)
- Sunday's Team Manager review files a `[META]` ticket. Read it carefully — it's the team's self-assessment.
- Pattern Detector Monday 11 AM files weekly themes ticket.
- Compare your "approve rate per agent" to baseline. Anything under 30% approve rate = agent needs tuning.

**Goal**: at least 7 days of execution, identify any agent that's consistently wrong, give Claude the feedback.

### Day 30
- Sunday Team Manager will have 4 weeks of patterns to compare. Trend lines emerge.
- Anchor #6 may be drafted by Content Producer (if you haven't drafted it yourself).
- Backlink outreach should have produced 8-10 drafts forwarded → expect 1-2 real responses by week 4.
- LinkedIn personal feed should have 12 posts (4 weeks × 3/week).
- SEM Manager will have proposed ~20 bid changes. Track which you actually applied.

**Goal**: data to know whether the system pays for itself. If yes, scale up. If no, kill the weakest agents.

---

## Frequently Asked Questions

**Q: Do I need to be at my computer at 6:30 AM?**
No. Agents run on n8n's schedule. Read the cards whenever you wake up.

**Q: What happens if I never read the dashboard?**
Agents keep running. The queue grows. Triage Digest still picks top 5 daily — those are the priorities. Team Manager weekly review surfaces what's broken. Nothing breaks — but you lose the value of training the agents.

**Q: Can I make agents work weekends?**
Most already do (Brand Listening, Asset Review, Backlink Poster). The exceptions (SEM Manager, Competitor SERP) only fire weekdays because that's when Google Ads has data. Tell me if you want weekend coverage on specific ones.

**Q: How much does this cost to run?**
- n8n on Hetzner: ~$5–20/month (your existing server)
- Claude API: ~$5–15/week (Opus for Team Manager, Sonnet 4.5 for everything else)
- Anthropic costs scale with agent firings — current 13 agents = ~$2–3/day
- HubSpot: existing
- Ahrefs / GSC / Google Ads APIs: existing

Total: roughly $80–150/month assuming current cadences.

**Q: What if Claude (the agent brain) is down?**
Agents that need LLM analysis (Content Producer, Coordinator, Pattern Detector, Team Manager) will error out. Agents that are rule-based (SEM Manager fallback, SEO Optimizer, UI/UX) keep working. The Agent Failure Alerts workflow files HIGH priority tickets when this happens.

**Q: What if I forget the kill switch URL?**
It's in this guide. Bookmark this file. Or just deactivate workflows one-by-one in https://agents.liftnowdirect.com.

**Q: Can I delete an agent permanently?**
Yes — in n8n, archive the workflow. It stops firing but data is preserved. Tell me (Claude) and I'll remove its references from other agents.

---

## Glossary

- **Agent** — one autonomous worker (an n8n workflow that runs on a schedule)
- **Receipt** — a ticket in HubSpot showing what an agent DID (not what it wants to do)
- **Pending** — a ticket waiting for your decision (rare in autonomous mode)
- **AUTO_EXECUTED** — outcome notation meaning "agent did this without asking"
- **Decision Memory** — webhook that lets agents check your prior rejections before proposing similar things
- **Self-Reject Gate** — internal check inside an agent that blocks its own output if it violates voice rules
- **Pillar** — one of 5 anchor topics on liftnow.com/resources/
- **Cluster page** — a sub-topic article under a pillar
- **Kill list** — products/topics agents must never propose (2-post, scissor, etc.)
- **MCC** — Google Ads Manager account (your 4271006215)
- **GAQL** — Google Ads Query Language

---

## Need Help?

I'm Claude. Open a chat with me and tell me:
- What you saw
- What you expected
- What you want fixed

Most issues take me <10 minutes to diagnose + fix via the n8n API. Common asks:
- "Build me a new agent that does X"
- "Agent Y keeps proposing Z, make it stop"
- "Change the schedule of agent Y"
- "What's the agent team's approve rate this week?"

---

**You're ready. Sleep well. Tomorrow you wake up to a team that's already done a day's work.**
