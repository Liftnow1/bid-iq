# Daily auto-ingest watcher

Walks `data/` once per day, finds files that are new or have changed since
the last run, and ingests them into the knowledge base via
`scripts/pillar3-runner.process_file` (native text + vision fallback).

## Files

- `scripts/watch-data-folder.py` — the watcher itself.
- `scripts/install-watcher-task.ps1` — one-shot Windows Task Scheduler setup.
- `data/.ingest-manifest.json` — written by the watcher; tracks one entry
  per seen file (path, mtime, size, last result, db row id). Don't edit by
  hand. If you want to force a re-ingest of everything, delete this file.
- `data/.ingest-log/YYYY-MM-DD.md` — one daily log file per run, listing
  what was ingested and at what cost. Review these if a query feels off.

## Install (one time)

**Step 1 — bootstrap.** Mark every file currently in `data/` as
already-ingested in the manifest, so the daily watcher only catches
genuinely new files. There are ~3000 supported files in `data/` already
covered by prior batch ingest runs; without bootstrap, the first
scheduled run would re-ingest all of them (~$3 + several hours and
likely DB duplicates). Run this once:

```powershell
python scripts\watch-data-folder.py --bootstrap
```

Expected output: `[watcher] bootstrap: marked ~3000 existing files as already-seen.`

**Step 2 — register the schedule.**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-watcher-task.ps1
```

That creates a scheduled task **`BidIQ Daily Ingest`** that runs every day
at **03:00 local time** as your user (no admin / no stored password). It
runs hidden — no console window appears. If your laptop's asleep at 03:00,
the task fires when it next wakes (`StartWhenAvailable`).

## Run it manually

Once installed, kick off a run any time:

```powershell
Start-ScheduledTask -TaskName "BidIQ Daily Ingest"
```

Or invoke the script directly:

```powershell
python scripts\watch-data-folder.py
python scripts\watch-data-folder.py --dry-run     # plan only
python scripts\watch-data-folder.py --verbose     # per-file details
python scripts\watch-data-folder.py --max-files 5 # cap files per run
```

## What gets ingested

- Anything under `data/` with a supported extension (`.pdf`, `.docx`,
  `.pptx`, `.md`, `.xlsx`).
- Tier is auto-derived from the path:
  - `data/pillar3-staging/Tier 1 - Public/...`     → `tier-1-public`
  - `data/pillar3-staging/Tier 2 - Internal/...`   → `tier-2-internal`
  - `data/pillar3-staging/Tier 3 - Paul-only/...`  → `tier-3-paul-only`
  - `data/product_data/<brand>/...`                → `tier-1-public`
  - everything else                                → `tier-2-internal` (default)

## What gets skipped (silently)

- Office lock files (`~$<name>.docx`)
- Hidden files (`.gitkeep`, dot-files, underscore-prefix `_DEDUP-*.csv`)
- Reference data (`*.json`, `*.csv`, `*.db`, `*.log`, `*.zip`)
- Unsupported extensions
- Files whose mtime + size haven't changed since last successful ingest

## Cost / safety

- Estimated cost per file: ~$0.001 native-text + vision fallback
- The pillar3-runner already logs every ingest into the existing
  ingest-log CSV, so this watcher's runs show up alongside manual runs
- `--max-files N` will cap a runaway run if you accidentally drop 1000
  files into `data/` overnight
- The watcher returns exit code 0 even if some files fail individually;
  failures are logged to the daily log and the manifest entry's
  `last_result` field. Re-running the watcher will retry those files
  (failures aren't marked "success" in the manifest).

## Remove

```powershell
Unregister-ScheduledTask -TaskName "BidIQ Daily Ingest" -Confirm:$false
```
