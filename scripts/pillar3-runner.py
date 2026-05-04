"""Pillar 3 ingest runner.

Walks data/pillar3-staging/, runs format-specific extraction, verifies
classification (folder-primary, classifier-secondary), and writes via
upsert_knowledge_item with Pillar-3 overrides.

Outputs:
    logs/pillar3-ingest.csv                  per-file row log
    logs/pillar3-summary.json                running totals
    logs/pillar3-classifier-disagreements.csv folder-vs-classifier diffs

Usage:
    python scripts/pillar3-runner.py --dry-run   # extract + classify, no DB writes
    python scripts/pillar3-runner.py             # live run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


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

from bidiq.ingest_pillar3 import (  # noqa: E402
    EXTRACTOR_VERSION_PILLAR3,
    SOURCE_TYPE_PILLAR3,
    Pillar3File,
    discover_pillar3_files,
    extract_file,
    get_liftnow_brand_id,
    repo_relative_path,
    verify_classification,
    write_pillar3_row,
)


LOGS_DIR = REPO_ROOT / "logs"
INGEST_CSV = LOGS_DIR / "pillar3-ingest.csv"
DISAGREE_CSV = LOGS_DIR / "pillar3-classifier-disagreements.csv"
SUMMARY_JSON = LOGS_DIR / "pillar3-summary.json"

INGEST_FIELDS = [
    "timestamp",
    "filepath",
    "tier_folder_derived",
    "tier_classifier_derived",
    "agreement",
    "extension",
    "char_count_extracted",
    "page_count",
    "api_cost_estimate",
    "result",
    "error_message",
    "db_row_id",
]

DISAGREE_FIELDS = [
    "filename",
    "folder_tier",
    "classifier_tier",
    "extension",
    "char_count",
    "extracted_text_first_500_chars",
]


def ensure_log_files(dry_run: bool) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if not INGEST_CSV.exists() or dry_run:
        with open(INGEST_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=INGEST_FIELDS).writeheader()
    if not DISAGREE_CSV.exists() or dry_run:
        with open(DISAGREE_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=DISAGREE_FIELDS).writeheader()


def append_ingest_row(row: dict) -> None:
    with open(INGEST_CSV, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=INGEST_FIELDS).writerow(row)


def append_disagree_row(row: dict) -> None:
    with open(DISAGREE_CSV, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=DISAGREE_FIELDS).writerow(row)


def write_summary(data: dict) -> None:
    with open(SUMMARY_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def process_file(
    f: Pillar3File,
    *,
    client: anthropic.Anthropic,
    model: str,
    db_url: str,
    brand_id: int,
    dry_run: bool,
) -> dict:
    """Returns a dict with the recorded row + metadata. Caller updates totals."""
    started = time.time()
    rel = repo_relative_path(f.path)
    row: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "filepath": rel,
        "tier_folder_derived": f.tier,
        "tier_classifier_derived": "",
        "agreement": "",
        "extension": f.ext.lstrip("."),
        "char_count_extracted": 0,
        "page_count": 0,
        "api_cost_estimate": 0.0,
        "result": "",
        "error_message": "",
        "db_row_id": "",
    }

    # --- Extract ---
    try:
        text, page_count, ext_cost = extract_file(f, client, model)
    except Exception as e:
        row["result"] = "failure"
        row["error_message"] = f"extraction crash: {type(e).__name__}: {e}"
        return row

    if not text or not text.strip():
        row["result"] = "failure"
        row["error_message"] = "extraction returned empty text"
        return row

    row["char_count_extracted"] = len(text)
    row["page_count"] = page_count
    row["api_cost_estimate"] = ext_cost

    # --- Classifier verification ---
    classifier_tier, agreement = verify_classification(
        client,
        folder_tier=f.tier,
        title=f.path.stem,
        extracted_text=text,
        model=model,
    )
    row["tier_classifier_derived"] = classifier_tier
    row["agreement"] = "TRUE" if agreement else "FALSE"
    # rough +$0.001 for the classify call
    row["api_cost_estimate"] = round(ext_cost + 0.001, 4)

    if not agreement:
        append_disagree_row(
            {
                "filename": f.path.name,
                "folder_tier": f.tier,
                "classifier_tier": classifier_tier,
                "extension": f.ext.lstrip("."),
                "char_count": len(text),
                "extracted_text_first_500_chars": text[:500].replace("\n", " "),
            }
        )

    # --- DB write (skipped on dry-run) ---
    if dry_run:
        row["result"] = "dry-run"
        return row

    try:
        with psycopg.connect(db_url) as conn:
            row_id = write_pillar3_row(
                conn,
                f=f,
                extracted_text=text,
                page_count=page_count,
                brand_id=brand_id,
            )
        row["db_row_id"] = row_id
        row["result"] = "success"
    except Exception as e:
        row["result"] = "failure"
        row["error_message"] = f"db write failed: {type(e).__name__}: {e}"
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description="Pillar 3 ingest runner")
    ap.add_argument("--dry-run", action="store_true",
                    help="Extract + classify but do not write to DB")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap number of files (testing)")
    ap.add_argument("--retry-failures", action="store_true",
                    help="Retry any failed files once after the first pass")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL") or ""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    if not db_url or not api_key:
        print("ERROR: DATABASE_URL or ANTHROPIC_API_KEY missing in env", file=sys.stderr)
        sys.exit(2)
    model = "claude-sonnet-4-20250514"

    files = discover_pillar3_files()
    if args.limit:
        files = files[: args.limit]
    if not files:
        print("No Pillar 3 files discovered.", file=sys.stderr)
        sys.exit(1)

    print(f"[pillar3] discovered {len(files)} files (dry_run={args.dry_run})")
    from collections import Counter
    by_tier = Counter(f.tier for f in files)
    by_ext = Counter(f.ext for f in files)
    print(f"  by tier: {dict(by_tier)}")
    print(f"  by ext:  {dict(by_ext)}")

    ensure_log_files(args.dry_run)

    client = anthropic.Anthropic(api_key=api_key)

    brand_id = 0
    if not args.dry_run:
        with psycopg.connect(db_url) as conn:
            brand_id = get_liftnow_brand_id(conn)
        print(f"  liftnow brand_id = {brand_id}")

    started_at = datetime.now(timezone.utc).isoformat()
    started_t = time.time()

    counts = {"success": 0, "dry-run": 0, "failure": 0}
    disagreements = 0
    cost_total = 0.0
    failed_files: list[Pillar3File] = []

    for i, f in enumerate(files, start=1):
        row = process_file(
            f, client=client, model=model, db_url=db_url,
            brand_id=brand_id, dry_run=args.dry_run,
        )
        append_ingest_row(row)
        counts[row["result"]] = counts.get(row["result"], 0) + 1
        cost_total += row["api_cost_estimate"]
        if row["agreement"] == "FALSE":
            disagreements += 1
        if row["result"] == "failure":
            failed_files.append(f)
        elapsed = time.time() - started_t
        marker = "DRY" if args.dry_run else row["result"][:3].upper()
        print(
            f"  [{i}/{len(files)}] {marker:3s} ({elapsed:5.1f}s) "
            f"folder={f.tier} cls={row['tier_classifier_derived'] or '-'} "
            f"chars={row['char_count_extracted']} "
            f"ext={f.ext} {f.path.name}"
        )

    if args.retry_failures and failed_files:
        print(f"[pillar3] retrying {len(failed_files)} failures (single pass)...")
        for f in failed_files:
            row = process_file(
                f, client=client, model=model, db_url=db_url,
                brand_id=brand_id, dry_run=args.dry_run,
            )
            append_ingest_row(row)
            counts[row["result"]] = counts.get(row["result"], 0) + 1
            cost_total += row["api_cost_estimate"]
            if row["result"] == "success":
                counts["failure"] -= 1

    elapsed = time.time() - started_t
    summary = {
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "files_discovered": len(files),
        "result_counts": counts,
        "disagreement_count": disagreements,
        "estimated_cost_usd": round(cost_total, 4),
        "elapsed_minutes": round(elapsed / 60, 2),
        "by_tier": dict(by_tier),
        "by_extension": dict(by_ext),
        "extractor_version": EXTRACTOR_VERSION_PILLAR3,
        "source_type": SOURCE_TYPE_PILLAR3,
    }
    write_summary(summary)

    print(
        f"[pillar3] DONE "
        f"success={counts.get('success',0)} dryrun={counts.get('dry-run',0)} "
        f"fail={counts.get('failure',0)} "
        f"disagreements={disagreements} "
        f"cost=${cost_total:.2f} "
        f"elapsed={elapsed/60:.1f}min"
    )


if __name__ == "__main__":
    main()
