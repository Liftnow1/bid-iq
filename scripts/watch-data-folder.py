"""Daily auto-ingest watcher for the local /data folder.

Walks data/ once, finds files that haven't been ingested yet (or whose mtime
has changed since last seen), and runs each through pillar3-runner.process_file
which handles native-text extraction with vision fallback. New rows go into
the knowledge_items DB table immediately — auto mode, no review step.

Per Paul's confirmed defaults (silent / auto):
  - No prompt for confirmation
  - Writes a daily log file at data/.ingest-log/YYYY-MM-DD.md so you can
    review what was ingested if you want
  - Maintains a manifest at data/.ingest-manifest.json with one entry per
    seen file (path, mtime, size, last_result, db_row_id)

Tier derivation:
  - data/pillar3-staging/Tier 1 - Public/...        -> tier-1-public
  - data/pillar3-staging/Tier 2 - Internal/...      -> tier-2-internal
  - data/pillar3-staging/Tier 3 - Paul-only/...     -> tier-3-paul-only
  - data/product_data/<brand>/...                   -> tier-1-public (carry-brand product spec)
  - everything else                                 -> tier-2-internal (default)

Skip rules (silent — never even logged unless --verbose):
  - Microsoft Office lock files: ~$<name>.docx
  - Hidden / underscore-prefix files (.gitkeep, _DEDUP-*.csv, etc.)
  - Reference data: *.json, *.csv, *.db, *.log (we don't try to extract these)
  - Zip archives: no direct extractor wired
  - Unsupported extensions (anything not in pillar3-runner's SUPPORTED_EXTS)

Usage:
    python scripts/watch-data-folder.py            # do the ingest
    python scripts/watch-data-folder.py --dry-run  # plan only
    python scripts/watch-data-folder.py --verbose  # log skips + per-file details

Schedule on Windows (one-time setup, separate command):

    # As Paul, in PowerShell. Runs daily at 03:00 local time.
    schtasks /Create /SC DAILY /ST 03:00 /TN "BidIQ Daily Ingest" ^
      /TR "powershell -NoProfile -WindowStyle Hidden -Command ^
           cd C:\\Users\\Paul\\bid-iq; ^
           python scripts\\watch-data-folder.py >> data\\.ingest-log\\schtasks.log 2>&1"

The schtasks command is included in the README of this branch.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"
MANIFEST_PATH = DATA_ROOT / ".ingest-manifest.json"
LOG_DIR = DATA_ROOT / ".ingest-log"


def _load_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    pattern = re.compile(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.+?)\s*$")
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            m = pattern.match(line)
            if m and not os.environ.get(m.group(1)):
                os.environ[m.group(1)] = m.group(2)


_load_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import anthropic  # noqa: E402
import psycopg  # noqa: E402

# Hyphenated module name "pillar3-runner" — load via importlib.
_runner_spec = importlib.util.spec_from_file_location(
    "pillar3_runner", REPO_ROOT / "scripts" / "pillar3-runner.py"
)
runner = importlib.util.module_from_spec(_runner_spec)
_runner_spec.loader.exec_module(runner)  # type: ignore[union-attr]

from bidiq.ingest_pillar3 import (  # noqa: E402
    SUPPORTED_EXTS,
    Pillar3File,
    get_liftnow_brand_id,
)


# Extensions we deliberately skip even though they're "supported" in the
# loose sense. These are reference data, not documents.
SKIP_EXTS = {".json", ".csv", ".db", ".log", ".zip", ".gz", ".tar"}

# Path prefixes (under data/) that the watcher must NEVER classify, because
# they're working files for other pipelines, not knowledge content. Review
# xlsx round-trips, intermediate parser dumps, and backup snapshots all
# belong here. data/pillar3-staging/ stays ingestible — that's Paul's
# intentional drop-zone for tier-3 source documents.
SKIP_PATH_PREFIXES = (
    "data/pillar2-staging/",   # round-trip xlsx review files (export/import)
    "data/scrapes/",           # raw web scrapes — not KB content
    "data/extraction-cache/",  # parser intermediate cache, if it ever appears
)


@dataclass
class ManifestEntry:
    path: str                          # repo-relative path, forward-slashed
    mtime: float                       # last modified time at ingest
    size: int                          # file size at ingest
    last_result: str                   # "success" | "failure" | "skip" | "dry-run"
    content_hash: Optional[str] = None # sha256 of the file bytes — primary change detector
    db_row_id: Optional[int] = None
    error_message: Optional[str] = None
    ingested_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, "")}


def compute_content_hash(p: Path) -> Optional[str]:
    """Return the sha256 hex digest of a file, or None on read error.

    Streamed in 1 MB chunks so we don't load multi-hundred-MB PDFs into RAM.
    Hashing 1 MB takes ~5 ms on this machine — even 7,000 files is well
    under a minute, and it's all local I/O (no API spend).
    """
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def load_manifest() -> dict[str, ManifestEntry]:
    if not MANIFEST_PATH.exists():
        return {}
    try:
        raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[warn] manifest unreadable, starting fresh: {e}", file=sys.stderr)
        return {}
    out: dict[str, ManifestEntry] = {}
    for k, v in raw.items():
        try:
            out[k] = ManifestEntry(
                path=v["path"],
                mtime=float(v["mtime"]),
                size=int(v["size"]),
                last_result=v.get("last_result", ""),
                content_hash=v.get("content_hash"),
                db_row_id=v.get("db_row_id"),
                error_message=v.get("error_message"),
                ingested_at=v.get("ingested_at", ""),
            )
        except Exception:
            continue
    return out


def save_manifest(m: dict[str, ManifestEntry]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    serial = {k: v.to_dict() for k, v in m.items()}
    MANIFEST_PATH.write_text(
        json.dumps(serial, indent=2, sort_keys=True), encoding="utf-8"
    )


def derive_tier_for_path(rel: Path) -> str:
    """Map a repo-relative file path under data/ to a tier string.

    The pillar3 ingester writes 'tier-1-public' / 'tier-2-internal' /
    'tier-3-paul-only' into knowledge_items.category. Default to internal.
    """
    parts = [p.lower() for p in rel.parts]
    pj = "/".join(parts)
    if "tier 1" in pj or "tier-1" in pj:
        return "tier-1-public"
    if "tier 3" in pj or "tier-3" in pj or "paul-only" in pj or "paul_only" in pj:
        return "tier-3-paul-only"
    if "tier 2" in pj or "tier-2" in pj:
        return "tier-2-internal"
    if parts and parts[0] == "data" and len(parts) >= 2 and parts[1] == "product_data":
        # Manufacturer spec sheets / manuals sit under data/product_data/<brand>/
        # and are public-facing carry-brand content.
        return "tier-1-public"
    return "tier-2-internal"


def should_skip(p: Path, verbose: bool) -> Optional[str]:
    """Return a skip reason string if the path should be skipped, else None."""
    name = p.name
    if name.startswith("~$"):
        return "office-lock-file"
    if any(part.startswith((".", "_")) for part in p.parts):
        return "hidden-or-underscore"
    ext = p.suffix.lower()
    if ext in SKIP_EXTS:
        return f"reference-ext={ext}"
    if ext not in SUPPORTED_EXTS:
        return f"unsupported-ext={ext}"
    # Path-prefix skips for review / staging / scrape directories.
    try:
        rel = p.resolve().relative_to(REPO_ROOT)
    except (ValueError, OSError):
        return None
    rel_key = str(rel).replace("\\", "/")
    for prefix in SKIP_PATH_PREFIXES:
        if rel_key.startswith(prefix):
            return f"path-skip={prefix.rstrip('/')}"
    return None


def discover_changed_files(
    manifest: dict[str, ManifestEntry], verbose: bool
) -> tuple[list[Pillar3File], list[tuple[str, str]]]:
    """Walk data/ for files whose content has actually changed.

    Change detection is **content-hash first** (sha256 of bytes). mtime+size
    is unreliable: `git stash pop`, OneDrive, antivirus, even some Windows
    backup paths bump mtimes without changing content, and a watcher that
    keys on mtime will re-classify thousands of unchanged files at $40+ per
    surprise. Hash comparison is the source of truth.

    Logic:
      - prev hash recorded and matches disk hash → SKIP (truly unchanged)
      - prev hash recorded and differs           → INGEST (real edit)
      - no prev hash but size matches manifest   → SKIP and BACKFILL hash
                                                   (legacy bootstrap entry —
                                                    same size = almost
                                                    certainly same content;
                                                    avoids re-paying for
                                                    files we already saw)
      - no prev entry at all                     → INGEST (genuinely new)

    Returns:
      new_or_changed: list of Pillar3File records to ingest this run
      skipped:        list of (rel_path, reason) for visibility
    """
    files: list[Pillar3File] = []
    skipped: list[tuple[str, str]] = []
    if not DATA_ROOT.exists():
        print(f"[err] data root missing: {DATA_ROOT}", file=sys.stderr)
        return [], []

    backfilled = 0
    for p in DATA_ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(REPO_ROOT)
        rel_key = str(rel).replace("\\", "/")
        skip_reason = should_skip(p, verbose)
        if skip_reason is not None:
            skipped.append((rel_key, skip_reason))
            continue

        try:
            stat = p.stat()
        except OSError as e:
            skipped.append((rel_key, f"stat-failed:{e}"))
            continue

        prev = manifest.get(rel_key)

        # Case 1: known file with a recorded hash → compare hashes
        if prev is not None and prev.content_hash:
            disk_hash = compute_content_hash(p)
            if disk_hash is not None and disk_hash == prev.content_hash:
                continue  # truly unchanged
            # else: fall through to ingest

        # Case 2: known file from a legacy bootstrap (no hash recorded) but
        # the size matches what we saw → assume unchanged and backfill the
        # hash so future runs are deterministic. mtime is intentionally
        # ignored here.
        elif (
            prev is not None
            and prev.last_result == "success"
            and prev.size == stat.st_size
        ):
            disk_hash = compute_content_hash(p)
            if disk_hash is not None:
                manifest[rel_key] = ManifestEntry(
                    path=rel_key,
                    mtime=stat.st_mtime,
                    size=stat.st_size,
                    last_result=prev.last_result,
                    content_hash=disk_hash,
                    db_row_id=prev.db_row_id,
                    error_message=prev.error_message,
                    ingested_at=prev.ingested_at,
                )
                backfilled += 1
            continue  # treat as unchanged either way

        tier = derive_tier_for_path(rel)
        files.append(
            Pillar3File(
                path=p,
                tier=tier,
                ext=p.suffix.lower(),
                size_bytes=stat.st_size,
            )
        )
    if backfilled and verbose:
        print(f"[watcher] backfilled content_hash on {backfilled} legacy manifest entries")
    return files, skipped


def write_log(today: str, run_summary: dict, processed_rows: list[dict]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{today}.md"
    lines: list[str] = []
    if log_path.exists():
        lines.append(log_path.read_text(encoding="utf-8"))
        lines.append("\n---\n")
    lines.append(f"# Daily ingest run — {datetime.now(timezone.utc).isoformat()}\n")
    lines.append(f"- discovered: {run_summary['discovered']}")
    lines.append(f"- skipped:    {run_summary['skipped']}")
    lines.append(f"- success:    {run_summary['success']}")
    lines.append(f"- failures:   {run_summary['failures']}")
    lines.append(f"- est. cost:  ${run_summary['cost_total']:.3f}")
    lines.append(f"- elapsed:    {run_summary['elapsed_min']:.1f} min")
    if processed_rows:
        lines.append("\n## Files processed\n")
        for row in processed_rows:
            mark = (
                "OK"
                if row.get("result") == "success"
                else "FAIL"
                if row.get("result") == "failure"
                else "DRY"
            )
            cost = row.get("api_cost_estimate", 0.0)
            chars = row.get("char_count_extracted", 0)
            err = row.get("error_message", "")
            lines.append(
                f"- [{mark}] {row.get('filepath','')}  chars={chars} cost=${cost:.3f}  {err}".rstrip()
            )
    log_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily data-folder watcher")
    ap.add_argument("--dry-run", action="store_true",
                    help="Discover + plan, but do not extract or write to DB")
    ap.add_argument("--verbose", action="store_true",
                    help="Print per-file details (default: summary only)")
    ap.add_argument("--max-files", type=int, default=None,
                    help="Cap files per run (defensive against runaway costs)")
    ap.add_argument("--bootstrap", action="store_true",
                    help="First-time setup: walk data/ and mark every supported file as "
                         "already-ingested in the manifest WITHOUT actually ingesting. "
                         "Use this once at install time so the daily watcher only catches "
                         "files added AFTER bootstrap, not all 3000 files already in the KB.")
    ap.add_argument("--rehash", action="store_true",
                    help="Walk data/, recompute sha256 for every supported file currently "
                         "on disk, and store it in the manifest. Use this after a bulk "
                         "operation (git stash pop, OneDrive sync, mass copy) that bumped "
                         "mtimes without changing content — otherwise the next daily run "
                         "treats every touched file as 'changed' and re-pays Claude. "
                         "Does NOT call the Anthropic API.")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL") or ""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    # --rehash is a local-only operation; only DATABASE_URL is needed for
    # the regular ingest path. Skip the api_key check in rehash mode.
    if not db_url:
        print("[err] DATABASE_URL missing", file=sys.stderr)
        return 2
    if not args.rehash and not args.bootstrap and not api_key:
        print("[err] ANTHROPIC_API_KEY missing", file=sys.stderr)
        return 2

    manifest = load_manifest()

    # --rehash: walk data/, compute sha256 of every supported file, write
    # to manifest, save. No Claude calls. Idempotent.
    if args.rehash:
        rehashed = 0
        added = 0
        removed = 0
        seen_keys: set[str] = set()
        for p in DATA_ROOT.rglob("*"):
            if not p.is_file():
                continue
            if should_skip(p, args.verbose) is not None:
                continue
            try:
                stat = p.stat()
            except OSError:
                continue
            rel_key = str(p.relative_to(REPO_ROOT)).replace("\\", "/")
            seen_keys.add(rel_key)
            digest = compute_content_hash(p)
            if digest is None:
                continue
            prev = manifest.get(rel_key)
            if prev is None:
                manifest[rel_key] = ManifestEntry(
                    path=rel_key,
                    mtime=stat.st_mtime,
                    size=stat.st_size,
                    last_result="success",
                    content_hash=digest,
                    ingested_at=datetime.now(timezone.utc).isoformat() + " (rehash-add)",
                )
                added += 1
            elif prev.content_hash != digest:
                manifest[rel_key] = ManifestEntry(
                    path=rel_key,
                    mtime=stat.st_mtime,
                    size=stat.st_size,
                    last_result=prev.last_result or "success",
                    content_hash=digest,
                    db_row_id=prev.db_row_id,
                    error_message=prev.error_message,
                    ingested_at=prev.ingested_at,
                )
                rehashed += 1
        # Drop manifest entries for files that no longer exist on disk.
        # Without this the manifest grows forever as files get renamed.
        stale = [k for k in manifest if k not in seen_keys]
        for k in stale:
            del manifest[k]
            removed += 1
        save_manifest(manifest)
        print(
            f"[watcher] rehash: hashed_or_added={added}, "
            f"refreshed_existing={rehashed}, removed_stale={removed}, "
            f"total_manifest_entries={len(manifest)}"
        )
        return 0

    # Bootstrap mode: mark every currently-existing supported file as
    # already-ingested in the manifest, then exit. Caller can then enable
    # the daily schedule and the watcher will only catch files added AFTER
    # this point.
    if args.bootstrap:
        bootstrapped = 0
        for p in DATA_ROOT.rglob("*"):
            if not p.is_file():
                continue
            if should_skip(p, args.verbose) is not None:
                continue
            try:
                stat = p.stat()
            except OSError:
                continue
            rel_key = str(p.relative_to(REPO_ROOT)).replace("\\", "/")
            if rel_key in manifest:
                continue  # already known
            digest = compute_content_hash(p)
            manifest[rel_key] = ManifestEntry(
                path=rel_key,
                mtime=stat.st_mtime,
                size=stat.st_size,
                last_result="success",
                content_hash=digest,
                db_row_id=None,
                error_message=None,
                ingested_at=datetime.now(timezone.utc).isoformat() + " (bootstrap)",
            )
            bootstrapped += 1
        save_manifest(manifest)
        print(
            f"[watcher] bootstrap: marked {bootstrapped} existing files as already-seen. "
            f"Manifest at {MANIFEST_PATH}"
        )
        print(
            "[watcher] daily runs will now only ingest files added/changed AFTER this point."
        )
        return 0

    files, skipped = discover_changed_files(manifest, args.verbose)
    if args.max_files is not None and len(files) > args.max_files:
        print(
            f"[watcher] capping {len(files)} discovered → {args.max_files} per --max-files"
        )
        files = files[: args.max_files]

    print(
        f"[watcher] discovered={len(files)} new/changed, skipped={len(skipped)} "
        f"(dry_run={args.dry_run})"
    )
    if args.verbose and skipped:
        skip_counts = Counter(reason for _, reason in skipped)
        for reason, n in skip_counts.most_common():
            print(f"  skip {reason}: {n}")
    if not files:
        print("[watcher] nothing to ingest. Done.")
        # Still write a no-op log entry so Paul can see the watcher ran.
        write_log(
            datetime.now().strftime("%Y-%m-%d"),
            {
                "discovered": 0,
                "skipped": len(skipped),
                "success": 0,
                "failures": 0,
                "cost_total": 0.0,
                "elapsed_min": 0.0,
            },
            [],
        )
        return 0

    runner.ensure_log_files(args.dry_run)
    client = anthropic.Anthropic(api_key=api_key)
    brand_id = 0
    if not args.dry_run:
        with psycopg.connect(db_url) as conn:
            brand_id = get_liftnow_brand_id(conn)

    started = time.time()
    counts = {"success": 0, "failure": 0, "dry-run": 0}
    cost_total = 0.0
    processed_rows: list[dict] = []
    for i, f in enumerate(files, start=1):
        try:
            row = runner.process_file(
                f, client=client, model="claude-sonnet-4-20250514",
                db_url=db_url, brand_id=brand_id, dry_run=args.dry_run,
            )
        except Exception as e:
            row = {
                "filepath": str(f.path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "result": "failure",
                "error_message": f"watcher crash: {type(e).__name__}: {e}",
                "api_cost_estimate": 0.0,
                "char_count_extracted": 0,
            }
        processed_rows.append(row)
        runner.append_ingest_row(row)
        result = row.get("result", "failure")
        counts[result] = counts.get(result, 0) + 1
        cost_total += float(row.get("api_cost_estimate", 0) or 0)

        # Update manifest entry for this file. Always record a content_hash
        # so the next run does true content-based change detection.
        rel_key = str(f.path.relative_to(REPO_ROOT)).replace("\\", "/")
        manifest[rel_key] = ManifestEntry(
            path=rel_key,
            mtime=f.path.stat().st_mtime,
            size=f.size_bytes,
            last_result=result,
            content_hash=compute_content_hash(f.path),
            db_row_id=row.get("db_row_id") or None,
            error_message=row.get("error_message") or None,
            ingested_at=datetime.now(timezone.utc).isoformat(),
        )

        # Persist the manifest after every file. Previously it only saved
        # at the very end of a run, so a crash or kill mid-run lost ALL
        # progress and the next run re-paid Claude for every already-done
        # file. JSON write is fast enough (<100ms even at 5K entries).
        save_manifest(manifest)

        if args.verbose:
            mark = result[:3].upper()
            print(
                f"  [{i}/{len(files)}] {mark} chars={row.get('char_count_extracted',0)} "
                f"cost=${row.get('api_cost_estimate',0):.3f} {f.path.name}"
            )

    save_manifest(manifest)
    elapsed = time.time() - started

    summary = {
        "discovered": len(files),
        "skipped": len(skipped),
        "success": counts["success"],
        "failures": counts["failure"],
        "cost_total": cost_total,
        "elapsed_min": elapsed / 60.0,
    }
    write_log(datetime.now().strftime("%Y-%m-%d"), summary, processed_rows)
    print(
        f"[watcher] DONE success={summary['success']} fail={summary['failures']} "
        f"dryrun={counts['dry-run']} cost=${cost_total:.2f} "
        f"elapsed={summary['elapsed_min']:.1f}min"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
