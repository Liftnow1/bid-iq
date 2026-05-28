# Audit 05 — Vercel Helper Endpoint Health

**Endpoints under test:** `/api/check-redirect` and `/api/known-models` — both
are single points of failure for Bee per audit/02.

## Liveness check (executed during Phase 1)

```bash
$ curl -sw "%{http_code} %{time_total}s" -o /dev/null \
  "https://bid-iq-neon.vercel.app/api/check-redirect?url=https://liftnow.com/types-of-vehicle-lifts/"
200 1.952s

$ curl -sw "%{http_code} %{time_total}s" -o /dev/null \
  "https://bid-iq-neon.vercel.app/api/known-models?brands=bendpak"
200 1.841s
```

Both endpoints respond HTTP 200 in ~2s on cold-hit. Good baseline.

## Correctness check (executed during Phase 1)

### /api/check-redirect

```bash
$ curl -s "https://bid-iq-neon.vercel.app/api/check-redirect?url=https://liftnow.com/types-of-vehicle-lifts/"
{"ok":true,"status":301,"location":"/products/vehicle-lifts/","redirected":true}
```

✅ Correct: detects 301, returns destination. This is the response shape Bee's
Build LLM Payload v8 parses.

### /api/known-models

```bash
$ curl -s "https://bid-iq-neon.vercel.app/api/known-models?brands=bendpak,challenger,pks"
{"ok":true,"brands":{
  "bendpak":{"models":["10AP","A6S","A6W",...,"XPR-15...","XR-12000L"],"count":482},
  "challenger":{"models":["AR4015EAO",...,"VLE10","ZZ44218Q"],"count":325},
  "pks":{"models":["MCZC210-LPEQ",...,"PK22"],"count":121}
}}
```

✅ Correct: returns real model strings extracted from KB titles. Verified earlier
that the regex matches HDS-18 (real BendPak), PCL-18B (real BendPak), VLE10 (real
Challenger), CLHM-140/-190 (real Challenger), NOT CLHM-150 (which the LLM hallucinated yesterday).

## Bee's usage pattern

Bee fires daily at 6AM ET. Each run makes:
- 2 GET /api/known-models (Fetch Known Models node fires once per run, but item-fan-out can multiply)
- 0-N GET /api/check-redirect (only when a candidate page is selected and redirect-check fires; typically 1-2 per run)

In a normal week: ~14 calls/week to known-models, ~10-15 calls/week to check-redirect. Cold-start latency (~2s) is acceptable inside Bee's 90s execution budget.

## Missing: active monitoring

**No cron pings either endpoint outside of Bee's actual runs.** If `/api/known-models` breaks at 4AM, Bee finds out at 6AM, Paul finds out when nothing happens or when QA fails for weird reasons.

### P1 fix (per prompt Section 4 item #5)

Add an n8n workflow `Vercel Helpers Watchdog`:
- Cron every 15 min
- GET `/api/check-redirect?url=https://liftnow.com/&_=$ts` — expect 200 ok=true
- GET `/api/known-models?brands=bendpak` — expect 200 ok=true with >100 models
- If either fails: file a P0 HubSpot ticket assigned to Coordinator (or post to a known webhook); pause Bee via Master Kill Switch

### Bee should hard-fail when these are unreachable

Currently Bee's Build LLM Payload wraps `/api/check-redirect` in try/catch and stores `_redirectCheck.ok = false` on failure. Then it just proceeds. **That's silent degradation** — Bee patches potentially-redirected URLs as if they're canonical.

**Fix:** in Build LLM Payload, if `_redirectCheck.ok === false` and the upstream candidate URL is a known-likely-redirect (heuristic: matches old slug patterns), set `_inItem.skipped = true` and bail. Don't draft on uncertain redirect state.

Same for `/api/known-models` — if it returns ok=false or empty brands, Bee should refuse to draft (since the QA gate becomes effectively disabled).

## Helper code source

Both endpoints live in this repo at:
- `app/api/check-redirect/route.ts` — uses Node fetch with `redirect: 'manual'`
- `app/api/known-models/route.ts` — Neon SQL against `knowledge_items` table

Vercel auto-deploys on push to main. Last commits touching these:
- `e13cb83` — Add /api/check-redirect endpoint
- `057d062` — Add /api/known-models endpoint

Both stable for 24+ hours.

## Recommendation

P1 watchdog is straightforward — see audit/09-fix-plan.md for implementation steps.
