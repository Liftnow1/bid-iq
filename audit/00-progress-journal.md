# Progress Journal — Liftnow Agent Symphony

Operator: Claude (Opus 4.7 Max Effort, autonomous run)
Start: 2026-05-28
Operator prompt source: Paul's CLAUDE.md handoff (the "Symphony Operator" prompt)

## How to read this file

Most recent at the bottom. Each checkpoint includes verified completions (with
receipt), in-progress work, blockers, plan for next 90 min, and receipts. If
a claim has no receipt, it's UNVERIFIED. The Receipt Law (Law 1) governs.

---

## CHECKPOINT 00:00 — Kickoff

### Session intent
Phase 1 reconnaissance only. No behavioral changes until audit/00-08 exist.

### What's already understood
- 4 active marketing agents: Turtle, Owl, Bee, Eagle. AE conductor. 9 infra
  workflows. 23 deactivated. State documented in AGENT_TEAM_STATUS.md (root).
- Yesterday's wins (logged in commit 057d062, e13cb83, e8725a7): KB-grounded
  Bee, /api/check-redirect helper, full chain proven LIVE (page title on
  /products/vehicle-lifts/ changed to Bee-drafted "Commercial Vehicle Lifts
  for Fleet Garages" via WC name PATCH + Yoast template).
- Yesterday's open thread: yoast-rest-term-meta.php v3.1 includes Yoast
  indexable rebuild call; awaiting Paul to upload + verify the
  yoast_head_json.description refreshes.

### Plan for first 90 min (audit only — no agent changes)
1. Pull 5 active workflow JSONs from n8n API (Turtle, Owl, Bee, Eagle, AE)
2. Pull 7d execution history per agent + identify silent skips / errors
3. Pull 14d HubSpot tickets via MCP — focus on stage 1 stuck and stage 2 stuck
4. Pull /api/check-redirect + /api/known-models reachability + recent error
5. Walk bid-iq-neon.vercel.app/approvals dashboard, document UX state
6. Write audit/01-08 markdown artifacts
7. Promote next 90-min checkpoint with delta

### Receipts
- git log (10 commits) — output captured in session log above
- audit/, migrations/, app/lib/ directories created



---

## CHECKPOINT 21:29 ET (local) — Phase 1 audit complete

### Completed since kickoff (verified, with receipts)
- audit/00 through audit/09 written and committed to repo
- Pulled 12 workflow JSONs to audit/data/workflows/ (5 active + 7 infra)
- Pulled last 30 executions per active agent to audit/data/executions/
- Confirmed Vercel helpers /api/check-redirect and /api/known-models healthy
  (200 in ~2s each)
- Identified 3 URGENT bugs not in the prompt default plan:
  - URGENT-1: Owl Fetch Refresh Queue credential type mismatch (40% failure rate)
  - URGENT-2: Eagle Fetch Rendered HTML reads clobbered $json.fetchedUrl
  - URGENT-3: agent-proposals?bucket=done filter broken
- Identified 11 zombie tickets in HubSpot from deactivated agents
- Confirmed 18 Auto-Applied tickets in last 7d — AE chain working since id=0 fix
- /approvals dashboard live but has stale "10-agent" header text

### In progress
- (none — about to start Phase 3)

### Blocked / Needs Paul
- (none yet — Paul has Code Snippets plugin install pending, but not blocking
  audit/Phase 3 work on URGENT-1, URGENT-2, URGENT-3, P0-A, P0-B)

### Next 90 min plan (Block 1 of execution sequence)
- URGENT-1: Fix Owl Fetch Refresh Queue credential type
- URGENT-2: Fix Eagle Fetch Rendered HTML $json clobber
- URGENT-3: Fix agent-proposals bucket=done filter
- Each gets Section 5 loop with receipt before moving on
- Goal: 3 verified fixes, journal updated, no regressions on AE/Bee/Owl/Eagle

### Receipts attached
- audit/data/workflows/ — 12 JSON files
- audit/data/executions/ — 5 list files
- audit/00-08 markdown — all written
- audit/09-fix-plan.md — Phase 2 output
- Owl latest error pulled live: 2026-05-27T20:38:41 Fetch Refresh Queue credential
- Eagle latest error pulled live: 2026-05-27T15:11:40 Fetch Rendered HTML
- Bucket bug confirmed via two curls returning identical results


---

## CHECKPOINT 21:41 ET (local) — Block 1 complete

### Completed since last checkpoint (verified, with receipts)
- **URGENT-1 (Owl handoffUrl scope bug)** — removed misplaced REFRESH_QUEUE_COMPETITOR_FILTER block from Pick Next Piece. Receipt: Owl exec 2026-05-28T01:34:53 succeeded (5m 19s) after multiple prior errors stopped at the same line 54.
- **URGENT-2 (Eagle Fetch Rendered HTML)** — defensive URL with $('Filter Content Pages').itemMatching($itemIndex) + continueOnFail + neverError. Receipt: Eagle exec 2026-05-28T01:36:10 succeeded in 9s.
- **URGENT-3 (agent-proposals view=done filter)** — added 1363043699 to done/all stage lists + accept bucket alias. Receipt: curl shows 50 tickets including 8 in stage 1363043699 (Auto-Applied).
- **P0-A SFD activated** — patched: continueOnFail on 2 HTTP fetches + trimmed WF_TO_AGENT to 4 active producers. Receipt: manual fire returned `Scanned 4 producers: 250 fires, 100 tickets — all clean`.
- **P0-A AFA activated** — workflow JSON already correct (httpHeaderAuth credentials, no $json clobber). errorWorkflow=qMeBXIjguVuaKLLF was already set on all 5 active workflows. Receipt: workflow API now shows active=True.

### Active workflow count: 13 → 15 (added SFD + AFA)

### Honest corrections (Law 5)
- I initially flagged Owl's credential type mismatch as URGENT-1 from stale execution data. Re-pulling the workflow JSON showed the credential was already correct — the actual current bug was a different one (the scope error). The real fix targeted the actual current bug, not the stale finding.
- I initially curl'd `?bucket=done` and concluded the filter was broken site-wide. Reading dashboard code showed dashboard uses `?view=done` — bucket was my own param error. Real bug was the stage list missing 1363043699.

### In progress
- (about to start P0-B: agent_handoffs Neon table + dual-write)

### Blocked / Needs Paul
- None blocking Phase 3 work.
- Yoast mu-plugin v3 verification (P2-E) still pending Paul-side confirmation that v3 file was uploaded. Not blocking other work.

### Next 90 min plan (Block 2)
- P0-B step 1: add agent_handoffs table to lib/db.ts schema + migration
- P0-B step 2: add /api/agent-handoffs REST endpoints (POST/GET/consume)
- P0-B step 3: update AE Write Handoffs node to dual-write (legacy marker + new table)
- P0-B step 4: update Owl Fetch Refresh Queue to call new endpoint
- P0-B step 5: dry run — fresh Turtle ticket → approve → confirm row in agent_handoffs + Owl consumes

### Receipts attached
- n8n exec list: Owl 8013 (error, pre-fix), Owl post-fix at 01:34:53 success
- Eagle exec 01:36:10 success
- SFD activation: 200; manual fire response body Scanned 4 producers
- AFA activation: 200; active=True confirmed via workflow API


---

## CHECKPOINT 02:10 ET — Block 2 + start of Block 3

### Completed since last checkpoint (verified, with receipts)

**P0-B Neon-backed typed handoff contract — END-TO-END VERIFIED**
- **Step 1 (table+indexes)**: `agent_handoffs` table + 3 indexes added to `lib/db.ts` schema, committed b962d92, pushed to Vercel.
- **Step 2 (REST endpoints)**: `app/api/agent-handoffs/route.ts` (POST/GET) + `app/api/agent-handoffs/[id]/consume/route.ts` (POST consume) deployed to Vercel. Full lifecycle smoke-tested: POST → GET → consume → idempotent re-consume → GET shows consumed filtered out. All 4 endpoints behaving per spec.
- **Step 3 (AE dual-write)**: AE workflow `hpgbBAmRmqtsfr6g` Write Handoffs Code node PATCHed (code 524 → 2558 bytes). Both legacy `HANDOFF:{json}` marker AND POST to `/api/agent-handoffs` now fire. Receipt: AE exec 8055 (mode=webhook, status=success) at 01:53:33; agent_handoffs row id=3 created at 01:53:34.019 with `from_agent='Content Decay Detector'`, `to_agent='Content Producer'` (nickname→role map), `kind='refresh_url'`, `source_ticket_id='45546441764'`. executionNotes contains both legacy HANDOFF: marker AND `[handoff REST ok: id=3 created]` suffix.
- **Step 4 (Owl reads+consumes new endpoint)**: Owl workflow `d7YwC4ezub4g1LrI` PATCHed in 3 stages:
  - Fetch Refresh Queue: POST HubSpot search → GET `/api/agent-handoffs?to_agent=Content Producer&pending=true`
  - Pick Next Piece: prepended new-shape parsing (`refreshResp.handoffs[]`); legacy HANDOFF marker parsing kept as 30-day fallback
  - Added "Consume Handoff" HTTP node wired from Create Content Ticket, Self-Reject Skip Log, and Log Memory Skip (all 3 terminal paths)
  - Consume URL reads `refresh_handoff_id` from Pick Next Piece directly (Set nodes were stripping the field from the item)
  - Receipt: Owl exec 8059 — Fetch Refresh Queue returned `{ok:true, count:1, handoffs:[{id:3, to_agent:Content Producer, kind:refresh_url}]}`; Pick Next Piece returned `{refresh_mode:true, refresh_handoff_id:3, pick.url:hunter-dsp706…}`. Self-Reject Gate correctly rejected (Hunter is kill-list brand). Final dry-run (post URL-expression fix) pending receipt.

**Bonus find during Owl PATCH (audit Honesty Law)**: Owl's old `Fetch Refresh Queue` filtered HubSpot pipeline 0 stage=3 (Deferred) — but AE moves processed handoffs to stage `1363043699` (Auto-Applied). This means the legacy HANDOFF: marker path NEVER actually worked end-to-end — Owl was reading the wrong stage. The 4 unconsumed HANDOFFs in audit/03 weren't a "credential bug" — they were a stage filter bug. Switching to the typed REST endpoint fixed BOTH paths at once.

**P1-F zombie cleanup (12 tickets bulk-closed)**
- Pulled pending queue, filtered to {Coordinator, SEM Manager, Backlink Builder} (all deactivated agents).
- POST'd `/webhook/process-decision` with `decision=reject` + outcome_notes `ZOMBIE — agent 'X' deactivated, no executor available.` for each.
- Receipt: all 12 returned `{ok:true, newStage:"4"}`. Verified pending queue now contains only active agents (8 Bee + 6 Eagle + 1 Turtle = 15 tickets, down from 27).

### Honest corrections (Law 5)
- Owl's original `Fetch Refresh Queue` had TWO bugs: (a) wrong credential type (fixed yesterday) AND (b) wrong stage filter (stage 3 instead of 1363043699). The audit only caught (a). The Neon REST migration sidesteps both.
- First Owl re-fire (exec 8059) succeeded but Consume Handoff fired only on the wp_create path. Self-Reject termination wasn't consuming. Added 2 more incoming edges to Consume Handoff (from Self-Reject Skip Log + Log Memory Skip). Second re-fire (exec 8069) saw Consume Handoff fire — but with `invalid id` error because n8n `set` nodes strip the `refresh_handoff_id` field. Fixed by reading from Pick Next Piece directly via `$('Pick Next Piece (anchor-stuck priority)').first().json.refresh_handoff_id`.
- Two iterations on the same node before getting consume right. Documenting as Law 5 honest correction.

### In progress
- (Block 2 complete; about to start Block 3 in earnest)

### P0-B END-TO-END FINAL RECEIPT
- Owl exec **8091** (mode=webhook, start 02:08:24, stop 02:12:28, status=success).
- Consume Handoff output:
  `{"ok":true,"id":"3","consumed_at":"2026-05-28T02:12:27.730Z","result":"self-rejected: kill-list or quality gate","was_already_consumed":false}`
- Live row id=3 now shows `consumed_at=2026-05-28T02:12:27.730Z`, `consumed_by_execution_id=8091`, `result="self-rejected: kill-list or quality gate"`.
- `GET /api/agent-handoffs?pending=true` returns `count:0` — the queue is empty as expected.
- **The full Turtle → AE → agent_handoffs → Owl → consume chain works on a brand-new typed contract, not a regex-parsed marker. P0-B is done.**

### Next 90 min plan (Block 3)
- P1-C: Vercel Helpers Watchdog (new n8n workflow, cron 15min)
- P2-A: typed `app/lib/hubspot-stages.ts` constants module
- P1-E: hard-fail Bee on Vercel helper outage (per audit/05)
- P1-B: bound Eagle auto-patch authority (max 25/run, daily digest)

### Receipts attached
- git commits: b962d92 (lib/db.ts + agent_handoffs endpoints)
- Smoke-test curl chain (5 calls): id=1 created → existing → consume → re-consume → pending excludes consumed
- AE exec 8055 — Process & Route + Write Handoffs + downstream all green; full executionNotes contains both legacy + REST writes
- Owl exec 8059 — Fetch Refresh Queue returned new shape; Pick Next Piece picked refresh_handoff_id=3
- Owl exec 8069 — Consume Handoff fired but returned `{ok:false, error:"invalid id"}` (set-node strip bug, then fixed)
- 12 process-decision POSTs all returned newStage=4
- Pending queue before/after: 27 → 15 (active agents only)


---

## CHECKPOINT 02:22 ET — P1-C watchdog deployed + verified

### Completed since last checkpoint (verified, with receipts)

**P1-C — Vercel Helpers Watchdog**
- New n8n workflow created: id=`vZEd1pkEHX6L0jGB`, name=`Vercel Helpers Watchdog`, active=True
- Layout: Schedule(15min) | Manual Fire → Probe Check-Redirect (GET ?url=) → Probe Known-Models (GET ?brands=challenger) → Validate Responses (Code) → If Unhealthy? → [TRUE: Build Alert Body → Create HubSpot Ticket] | [FALSE: Log All Clean (Set)]
- errorWorkflow wired to Agent Failure Alerts (qMeBXIjguVuaKLLF)
- **Healthy-path receipt**: exec 8105 status=success, all 6 nodes ran, Validate Responses output `{unhealthy:false, reason:"all green", check_redirect_status:200, known_models_status:200, challenger_model_count:225}`
- **Failure-path receipt**: temporarily pointed Probe Known-Models at a 404 URL, fired watchdog → exec 8113 status=success → Create HubSpot Ticket returned id=45628243574 at 02:21:27. Then closed the test ticket via process-decision (newStage=4), reverted probe URL to good. Watchdog now back to healthy steady state.
- Used HubSpot-allowed enum values (agent_name=`Other`, recommendation_type=`Dashboard Insight`) and prepended `[Vercel Helpers Watchdog]` to subject — HubSpot has strict enums on those properties.

**P2-A — typed `lib/hubspot-stages.ts` module**
- Created. Exports: `PIPELINE_AGENT_TICKETS`, `STAGE_PENDING_REVIEW`, `STAGE_APPROVED`, `STAGE_DEFERRED`, `STAGE_REJECTED`, `STAGE_AUTO_APPLIED`. Helpers: `isOpenStage`, `isClosedStage`, `isAutoApplied`, `stageLabel`, `stagesForBucket` with bucket aliases for dashboard. No consumers yet — foundation for future Vercel-side refactors.

### Honest corrections (Law 5)
- First watchdog deploy used POST for /api/check-redirect (route is GET) and `?brand=challenger` singular (route is `?brands=` plural). HTTP 405 + parser miss. Fixed.
- First parallel fan-out from Manual Fire only triggered Probe Check-Redirect — Probe Known-Models silently didn't run. Switched to sequential to avoid n8n parallel-output ambiguity. Now: Manual Fire / Cron → Probe CR → Probe KM → Validate. Adds ~200ms latency, trades that for reliability.
- First HubSpot ticket creation 400'd because `recommendation_type='Infrastructure Alert'` and `agent_name='Vercel Helpers Watchdog'` weren't in HubSpot's enum. Fixed by using the existing `Other` / `Dashboard Insight` values + watchdog tag in subject.

### In progress
- (about to start P1-E: hard-fail Bee on Vercel helper outage)

### Next 90 min plan (rest of Block 3 / start Block 4)
- P1-E: Bee hard-fail when helpers are down (current behavior silently degrades — see audit/05)
- P1-B: Bound Eagle auto-patch authority (max 25/run + max 3/page + daily digest)
- P2-E: Yoast mu-plugin v3 verification (depends on Paul confirming the plugin file is uploaded)
- P1-G: Quick UX fixes — header agent count, Loading→error timeout

### Receipts attached
- Watchdog workflow JSON saved to audit/data/workflows/vercel_helpers_watchdog.json
- exec 8105 (healthy): all_helpers_green, 225 challenger models found
- exec 8113 (failed-on-purpose): HubSpot ticket 45628243574 created → closed (stage=4)
- 4 watchdog execs visible (8103 err, 8105 success, 8107 err, 8113 success) — error count expected during dry-runs, no real alerts to operator


---

## CHECKPOINT 02:35 ET — Block 3 done (P1-E + P1-B + partial P1-G)

### Completed since last checkpoint (verified, with receipts)

**P1-E — Bee hard-fail on Vercel helper outage**
- Bee `Build LLM Payload` Code node PATCHed (audit/data/workflows/bee.json updated; updatedAt 02:24:41).
- New behavior:
  - `Fetch Known Models` empty/errored → set `_inItem.skipped=true`, `return []` (halts downstream).
  - `/api/check-redirect` exception → set `skipped=true`, `return []`.
  - "Redirected to non-tracked destination" branch ALSO returns [] now (was previously letting downstream waste a draft).
- **Receipt**: Bee exec 8118 fired with Fetch Known Models pointed at a 404 URL. Build LLM Payload output **0 items**. Downstream nodes (Draft Fix, Parse + QA, Build Ticket, Create SEO Ticket) did NOT run. No HubSpot ticket created. After verification, probe URL reverted to good. Healthy steady state confirmed.

**P1-B — Eagle auto-patch authority bounds codified**
- Eagle `Build Auto-PATCH` Code node PATCHed (1896 → 3433 chars; audit/data/workflows/eagle.json updated; updatedAt 02:29:01).
- New constants: `MAX_AUTO_PATCHES_PER_RUN = 25`, `MAX_AUTO_PATCHES_PER_PAGE = 3`. Counters track `runTotal`. When per-run cap hit, pages get `noAutoFix:true, capReason:'per_run_cap_hit'`. Per-page cap stops applying more fixes when 3 reached. First output item carries `_runStats = {autoPatchesAppliedThisRun, autoPatchesSkippedByCapThisRun, cap_per_run, cap_per_page}`.
- **Cap-engagement receipt**: temporarily lowered `MAX_AUTO_PATCHES_PER_RUN=1`, fired Eagle (exec 8125). `_runStats = {autoPatchesAppliedThisRun:1, autoPatchesSkippedByCapThisRun:1, cap_per_run:1, cap_per_page:3}` — one fix applied, then runTotal hit cap and subsequent defects were skipped with reason='per_run_cap'. Reverted to 25.

**P1-G partial — UX quick wins**
- Header text: "10-agent marketing team" → "4 agents active · 23 paused" (public/approvals/index.html:313).
- Loading watchdog: added 15s setTimeout in `loadCurrentTab` that replaces a still-showing `.loading` with a friendly error+retry button. Prevents "stuck on Loading…" UX bug. clearTimeout in finally so successful loads don't trigger it.
- Zombie filter: NOT NEEDED at frontend layer because P1-F bulk-close already removed all 12 zombies at HubSpot level. Future re-activations would re-introduce; would need agent-name allowlist in the agent-proposals webhook. Deferred to next operator.

### In progress
- (None — about to roll into next 90-min plan)

### Next 90 min plan (Block 4)
- P1-D: Owl Self-Reject feedback loop (Neon `self_reject_log` table + endpoint + Owl wire)
- P1-A: Minimal Team Manager (daily cron report to paulj@liftnow.com)
- End-of-shift report draft (audit/99-end-of-shift.md)

### Receipts attached
- Bee exec 8118: Build LLM Payload output 0 items when KM was bad; downstream skipped
- Eagle exec 8125: _runStats showed cap_per_run=1 engagement
- audit/data/workflows/bee.json, eagle.json updated
- public/approvals/index.html — 1 text change + 1 watchdog block addition


---

## CHECKPOINT 02:45 ET — Block 4 (P1-D) + NEW-BUG-1 discovered

### Completed since last checkpoint (verified, with receipts)

**P1-D — Owl Self-Reject feedback loop**
- `self_reject_log` Neon table + 2 indexes added to lib/db.ts. Committed 4d6a0d4, deployed to Vercel.
- `/api/self-reject-log` endpoint: POST (write), GET (?agent / ?aggregate=top-failed-checks). Smoke-tested: POST id=2 created; aggregate returns ranked checks; list filters by agent.
- Owl "Log Self-Reject Detail" HTTP node added (18 → 19 nodes), wired from Self-Reject Skip Log, reads failed_checks + draft + word_count from Self-Reject Gate via named-node expressions.
- **Receipt**: Owl exec 8130 — Log Self-Reject Detail returned `{ok:true, id:3}`; Consume Handoff returned `{ok:true, id:4, consumed_at:..., result:"self-rejected: kill-list or quality gate"}`; self_reject_log now has rows with full failed_checks arrays (byline, toc, fleet_manager_callout, procurement_callout, faq, closing_tagline, cta, banned_competitors, hedge_density, word_count_ok); agent_handoffs id=4 consumed by exec 8130.

### 🔴 NEW-BUG-1 discovered (NOT introduced by me — pre-existing)

**Owl calls Claude 4× per run.** "Pick Next Piece (anchor-stuck priority)" has FOUR incoming main connections (Fetch Published Pages, GSC Anchor Performance, Fetch Published Posts, Fetch Refresh Queue) all into input 0. n8n runs a node once per delivering connection → Pick Next Piece fires 4×, which fans out to Generate Draft + LinkedIn (Claude call) 4× and Self-Reject Gate 4×.

- **Evidence**: execs 8059, 8091, 8130 ALL show Generate Draft + LinkedIn ran 4× and Pick Next Piece ran 4×. Fetch Refresh Queue ran 1× (correctly), so the fan-out is purely the 4 input connections.
- **Impact**: ~4× Anthropic API cost on every Owl run, ~4× runtime (explains the 4-5 min Owl cycles), and 4 near-identical drafts/self-rejects per run. Now also causes 4 duplicate self_reject_log rows per self-reject (the aggregate ranking stays correct since all checks inflate proportionally, but absolute counts are 4×).
- **Root cause**: Pick Next Piece's code reads the other 3 sources via `$("NodeName").first()` expressions, so they should NOT be main-input connections — they only need to have run earlier. Only ONE node should trigger Pick Next Piece (after all 4 have data).
- **Why I did NOT fix it now**: the clean fix needs a Merge node (or sequential re-chaining) + a full Owl test cycle to confirm the `$('Fetch Published Pages').first()` refs still resolve. Owl runs take 4-5 min each, so verifying would eat the rest of the shift, and a broken Owl breaks the whole content pipeline. Per Section 9 (no risky changes without time to verify) + Honesty Law, I'm flagging it rather than half-fixing it.
- **Handed off**: spawned a background task ("Fix Owl 4× Claude call (Pick Next Piece fan-in)") with full repro + fix guidance. This is the #1 recommended item for the next operator.

### In progress
- (None — wrapping shift)

### Receipts attached
- self_reject_log endpoint smoke chain (POST/GET aggregate/GET list)
- Owl exec 8130: Log Self-Reject Detail id=3, Consume Handoff id=4 consumed
- NEW-BUG-1 evidence: node-run multiplicity across execs 8059/8091/8130 (Generate Draft ran 4× each)
- Pick Next Piece connection dump: 4 main inputs confirmed


---

## CHECKPOINT 13:50 ET — NEW-BUG-1 fix verified + hardened + closed

### What happened
A spawned session fixed NEW-BUG-1 (Owl 4× Claude call) by re-chaining the four fetch nodes sequentially so Pick Next Piece fires once. They reported back; I verified per Law 1 (trust-but-verify) rather than taking the summary at face value.

### Verified (with receipts)
- **PNP single-fire confirmed**: live workflow now shows 1 incoming main connection into Pick Next Piece (was 4). Execs 8593 + 8626: Pick Next Piece, Generate Draft + LinkedIn each ran **1×** (was 4×). ~75% Owl token-cost cut, proven.
- **Chain order correct**: Tue 9AM / Manual Fire → Fetch Published Pages → GSC Anchor Performance → Fetch Published Posts → Fetch Refresh Queue → Pick Next Piece.
- **self_reject_log duplication gone at source**: exec 8626 wrote exactly 1 row (was 4).
- **`/api/agent-handoffs` healthy**: 200 in 1.3s. The 3 errored execs (8576/8578/8585) ALL predate the fix (13:15:32); the only post-fix runs (8593/8626) are green.
- **No rogue auto-fire**: only 1 `mode=trigger` exec (the weekly schedule); the rest are `mode=webhook` test fires. No `integrated`-mode caller.

### Hardening I added (the fix put a flaky node in the critical path)
- Could NOT reproduce the exact mid-flight "connection aborted" of exec 8585 — n8n's `neverError` masked every failure I injected (404 via Vercel wildcard DNS, `.invalid` TLD both came back "ok"). Rather than ship an unproven resilience claim, I made Fetch Refresh Queue maximally fault-tolerant: `neverError` + `onError:continueRegularOutput` + `retryOnFail` + `alwaysOutputData`. Confirmed green on exec 8626. A handoff-queue outage now degrades to the anchor-stuck picker instead of killing content drafting.
- **DB defense-in-depth (deployed, commit 23dec8b)**: `self_reject_log` NULL-tolerant unique index `(COALESCE(exec_id,''), COALESCE(url,''))` + idempotent pre-dedupe + endpoint treats 23505 as `{ok:true,deduped:true}`. Verified live: 2nd identical POST returned `deduped:true` once Vercel finished deploying.

### Cleanup (Law 5 — clean up after yourself)
- Deleted 4 synthetic verification rows from self_reject_log (agent='test'/'DedupTest', smoke/dedup/hunter-tc33m-rig URLs). Left the 6 genuine Owl cluster-page self-rejects (real check names feed the aggregate). Cleanup script in .tmp_n8n (gitignored).

### Receipts attached
- Owl live config: PNP 1 incoming conn; FRQ onError=continueRegularOutput, retryOnFail=True, alwaysOutputData=True, neverError=True
- exec 8593 + 8626: all nodes 1× , status success
- self_reject_log exec 8626 = 1 row; dedup probe attempt 6 = `{ok:true,deduped:true}`
- commit 23dec8b pushed; Vercel deployed (dedup live)


---

## CHECKPOINT 14:35 ET — Paul flagged Bee duplicates; SAME fan-out bug, fixed (with an honest stumble)

### Trigger
Paul sent a dashboard screenshot: 4× identical "Commercial Vehicle Lifts" SEO tickets + 3× "Vehicle Lifts" — "still confusing, sending off multiple hits."

### Diagnosis (receipts)
Same multi-input fan-out bug as Owl, now in **Bee**. "Filter Content Pages" had 5 incoming main connections (Get All Pages/Posts/Product Categories ×2/Known Models) → n8n ran the WHOLE Bee chain 5× → 5 identical tickets + 5× Claude in Draft Fix per run. Confirmed exec 8640: every node ran 5×; all 5 Build Ticket items were wpId 123 (same page). Filter Content Pages reads all sources via `$('Node').all()` in flatten(), so it only needs to fire once.

### 🔴 Honest stumble (Law 5)
First fix (v1): chained the 6 fetches sequentially like the Owl fix. **This OOM-crashed Bee** (exec 8670, "WorkflowCrashedError: possible out-of-memory"). Root cause: HTTP nodes run once PER INPUT ITEM, so chaining made Get All Posts receive 42 items and multiply → explosion (Owl didn't hit this because its data volumes are tiny; Bee pulls 270+ items). **I rolled Bee back to the known-good parallel topology immediately** (restored from .tmp_n8n/bee_backup_pre_chain.json, verified active). No tickets were created by the crashed run.

### Fix v2 (verified)
Chained the fetches BUT set `executeOnce:true` + `onError:continueRegularOutput` + `alwaysOutputData:true` on all 6 fetch nodes. executeOnce stops the per-item multiplication (each input-independent GET runs once). Receipt: exec 8679 status=**success**, every node ran **1×** (Filter Content Pages 1, Draft Fix 1 = 1× Claude, Create SEO Ticket 1 = ONE ticket). The single ticket is a high-quality draft (real models HD-14LSX/CL20, Sourcewell contract, dealer voice).

### Queue cleanup (receipts)
- Closed 10 SEO duplicates (kept newest per page: Commercial Vehicle Lifts, Types of Vehicle Lifts, Vehicle Lifts).
- Closed 6 stale Coordinator alerts (silent-failure tickets triggered by my Owl/Bee test-fire failures today; both agents now healthy).
- Closed 8 more accumulated dupes (7 Eagle "scan complete" across multiple runs + 1 Turtle hunter-tc33m).
- Pending queue: **36 → 12**, all distinct now (Content Decay Detector 7, SEO Optimizer 3, UI/UX Performance 2).

### Fan-out sweep across ALL active workflows
Only Owl + Bee had the HARMFUL version (per-run duplicate side effects). Checked every active workflow:
- Eagle "Filter Content Pages" <- 2 inputs → runs 2× BUT "Create Summary Ticket" runs 1× (a Merge Results node absorbs the branches; PATCH WordPress also 1×). So Eagle wastes a little scan compute but creates NO duplicate tickets / no double-PATCH. Benign — not fixing (the OOM risk isn't worth it for a cosmetic 2× scan).
- All other multi-input nodes are benign: schedule-trigger + manual-fire-webhook pairs (2 inputs, mutually exclusive) or genuine Merge nodes (Master Kill Switch 8, AE Merge All Branches 5, etc.).

### Still open (flagged, NOT auto-resolved)
- **Content Decay Detector / Hunter pages**: Turtle keeps proposing refreshes for liftnow.com pages targeting Hunter keywords (hunter-tc33m, hunter-wa673…). Owl self-rejects them (kill-list brand), so they churn. This is a STRATEGY question for Paul: keep the competitor-keyword arbitrage pages (needs a non-Owl content path) or retire/redirect them. Left in queue for his call.

### Receipts attached
- exec 8640 (pre-fix): all nodes 5×, 5 identical wpId-123 tickets
- exec 8670 (v1): crashed, OOM — rolled back
- exec 8679 (v2): success, all nodes 1×, 1 ticket
- process-decision: 16 + 8 = 24 dupes/stale closed, 0 errors; queue 36→12
- fan-out sweep output (Owl+Bee harmful, Eagle benign, rest benign)
- backup: .tmp_n8n/bee_backup_pre_chain.json
