"""Corpus-wide native-text re-extraction runner.

Targets two row populations:

1. Wave 1 carry-brand PDFs — `extractor_version = 'ingest.py-v1-tier1'`
   These were ingested with the cover+last-page-only path (search_text
   built from title+summary+tags only; raw_content is NULL). The 50-PDF
   spot-check showed ~86% of these PDFs have a clean native text layer
   that yields ~38× more searchable content for $0 / millisecond cost.

2. Pillar 3 PDFs — `source_type = 'pillar3_staging' AND content_type = 'pdf'`
   The original Pillar 3 live run used pure vision extraction. Re-running
   them through the native-first path (prefer_native_text=True is the
   default in the current ingester) recovers any content that vision
   missed — same pattern that turned BendPak's contract from 3,048 chars
   into 299,225 chars.

Behavior per row:
- Read the source PDF, run pypdf-based native text extraction (with
  multipart-envelope fallback for the Mohawk-extension-style files).
- If avg chars/page >= MIN_CHARS_PER_PAGE: rich UPDATE in place.
  - raw_content   <- full native body
  - search_text   <- title + summary + tags + body (Wave 1 tier-2 convention)
  - extractor_version <- 'ingest.py-v1-native' (Wave 1) or kept as
    'ingest.py-v1-pillar3-full' (Pillar 3) so the existing classification
    of which workflow ingested the row stays accurate.
  - extracted_at  <- NOW()
- Else: skip (PDF is image-only / scan / drawing — vision was correct).
- Existing title, summary, tags, category, brand_id, source_*: untouched.

Output:
    logs/native-reextract.csv    per-row log
    logs/native-reextract-summary.json

Usage:
    python scripts/native-reextract-runner.py --dry-run
    python scripts/native-reextract-runner.py --population wave1
    python scripts/native-reextract-runner.py   # both populations
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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

import psycopg  # noqa: E402

from bidiq.ingest import _build_search_text  # noqa: E402
from bidiq.ingest_pillar3 import extract_pdf_native_text  # noqa: E402


LOGS_DIR = REPO_ROOT / "logs"
CSV_PATH = LOGS_DIR / "native-reextract.csv"
SUMMARY_PATH = LOGS_DIR / "native-reextract-summary.json"

CSV_FIELDS = [
    "timestamp",
    "row_id",
    "population",
    "source_filename",
    "source_path",
    "page_count",
    "old_search_chars",
    "new_search_chars",
    "native_body_chars",
    "status",
    "new_extractor_version",
    "error_message",
]

# Per-row text-layer threshold below which we leave the row alone.
MIN_CHARS_PER_PAGE = 200


def fetch_targets(conn: psycopg.Connection, population: str) -> list[dict]:
    """Return the row metadata we need to drive each re-extract."""
    where_parts: list[str] = []
    params: list = []
    if population == "wave1":
        where_parts.append(
            "source = 'ingested' AND source_type = 'ingested_pdf' "
            "AND extractor_version = 'ingest.py-v1-tier1'"
        )
    elif population == "pillar3":
        where_parts.append(
            "source = 'ingested' AND source_type = 'pillar3_staging' "
            "AND content_type = 'pdf'"
        )
    elif population == "both":
        where_parts.append(
            "(source = 'ingested' AND source_type = 'ingested_pdf' "
            "AND extractor_version = 'ingest.py-v1-tier1') "
            "OR (source = 'ingested' AND source_type = 'pillar3_staging' "
            "AND content_type = 'pdf')"
        )
    else:
        raise ValueError(f"unknown population: {population}")

    sql = f"""
        SELECT id, source_filename, source_path, source_type,
               coalesce(title,'') AS title,
               coalesce(summary,'') AS summary,
               coalesce(tags, '{{}}'::text[]) AS tags,
               length(coalesce(search_text,'')) AS old_search_chars,
               extractor_version
          FROM knowledge_items
         WHERE {' OR '.join(where_parts) if where_parts else 'TRUE'}
         ORDER BY id
    """
    out: list[dict] = []
    with conn.cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            (rid, fn, src_path, src_type, title, summary, tags,
             old_chars, ver) = row
            pop = "wave1" if src_type == "ingested_pdf" else "pillar3"
            out.append({
                "id": rid,
                "source_filename": fn,
                "source_path": src_path,
                "source_type": src_type,
                "title": title,
                "summary": summary,
                "tags": list(tags) if tags else [],
                "old_search_chars": int(old_chars or 0),
                "old_extractor_version": ver,
                "population": pop,
            })
    return out


def update_row(
    conn: psycopg.Connection,
    *,
    row_id: int,
    raw_content: str,
    search_text: str,
    new_version: str,
    page_count: int,
) -> None:
    """Targeted UPDATE — title/summary/tags/category/brand_id/source_* preserved."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE knowledge_items SET
                raw_content = %s,
                search_text = %s,
                source_pages_count = %s,
                extractor_version = %s,
                extracted_at = NOW()
              WHERE id = %s
            """,
            (raw_content, search_text, page_count, new_version, row_id),
        )
        conn.commit()


def ensure_csv_header() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if not CSV_PATH.exists():
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()


def append_csv_row(row: dict) -> None:
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)


def _process_one(
    target: dict,
    db_url: str,
    *,
    dry_run: bool,
) -> dict:
    """Worker: returns a CSV-ready row dict + status."""
    out: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "row_id": target["id"],
        "population": target["population"],
        "source_filename": target["source_filename"],
        "source_path": target["source_path"],
        "page_count": "",
        "old_search_chars": target["old_search_chars"],
        "new_search_chars": "",
        "native_body_chars": "",
        "status": "",
        "new_extractor_version": "",
        "error_message": "",
    }
    src = REPO_ROOT / target["source_path"]
    if not src.exists():
        out["status"] = "missing"
        out["error_message"] = "PDF not on disk"
        return out

    native = extract_pdf_native_text(src, min_chars_per_page=MIN_CHARS_PER_PAGE)
    if native is None:
        out["status"] = "skipped-vision-only"
        return out

    body = native["content_markdown"]
    page_count = native["page_count"]
    out["page_count"] = page_count
    out["native_body_chars"] = len(body)

    new_search = _build_search_text(
        target["title"], target["summary"], target["tags"], body
    )
    out["new_search_chars"] = len(new_search)

    # Population-specific extractor_version: Wave 1 rows get a fresh tag
    # so we can roll back / measure; Pillar 3 keeps its workflow tag.
    if target["population"] == "wave1":
        new_ver = "ingest.py-v1-native"
    else:
        new_ver = "ingest.py-v1-pillar3-full"
    out["new_extractor_version"] = new_ver

    if dry_run:
        out["status"] = "dry-run"
        return out

    try:
        with psycopg.connect(db_url) as conn:
            update_row(
                conn,
                row_id=target["id"],
                raw_content=body,
                search_text=new_search,
                new_version=new_ver,
                page_count=page_count,
            )
        out["status"] = "updated"
    except Exception as e:
        out["status"] = "db-error"
        out["error_message"] = f"{type(e).__name__}: {e}"
    return out


def run(population: str, *, dry_run: bool, concurrency: int, limit: Optional[int]) -> None:
    db_url = os.environ.get("DATABASE_URL") or ""
    if not db_url:
        print("ERROR: DATABASE_URL not in env", file=sys.stderr)
        sys.exit(2)

    with psycopg.connect(db_url) as conn:
        targets = fetch_targets(conn, population)
    if limit:
        targets = targets[:limit]

    by_pop: dict[str, int] = {}
    for t in targets:
        by_pop[t["population"]] = by_pop.get(t["population"], 0) + 1
    print(
        f"[native-reextract] targets: {len(targets)} "
        f"(by population: {by_pop})  dry_run={dry_run}  concurrency={concurrency}"
    )

    ensure_csv_header()
    started = time.time()
    counts: dict[str, int] = {}
    total_old = 0
    total_new = 0
    total_native = 0

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = {
            pool.submit(_process_one, t, db_url, dry_run=dry_run): t
            for t in targets
        }
        for i, fut in enumerate(as_completed(futs), start=1):
            try:
                row = fut.result()
            except Exception as e:
                t = futs[fut]
                row = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "row_id": t["id"], "population": t["population"],
                    "source_filename": t["source_filename"],
                    "source_path": t["source_path"], "page_count": "",
                    "old_search_chars": t["old_search_chars"],
                    "new_search_chars": "", "native_body_chars": "",
                    "status": "worker-crash",
                    "new_extractor_version": "",
                    "error_message": f"{type(e).__name__}: {e}",
                }
            append_csv_row(row)
            counts[row["status"]] = counts.get(row["status"], 0) + 1
            if row["status"] in ("updated", "dry-run"):
                total_old += row["old_search_chars"] or 0
                total_new += row["new_search_chars"] or 0
                total_native += row["native_body_chars"] or 0
            if i % 50 == 0 or i == len(targets):
                elapsed = time.time() - started
                print(
                    f"  [{i}/{len(targets)}] {elapsed:6.1f}s  "
                    f"updated={counts.get('updated',0)} "
                    f"skipped={counts.get('skipped-vision-only',0)} "
                    f"missing={counts.get('missing',0)} "
                    f"dry-run={counts.get('dry-run',0)} "
                    f"errs={counts.get('db-error',0)+counts.get('worker-crash',0)}"
                )

    elapsed = time.time() - started
    summary = {
        "started_at": datetime.fromtimestamp(time.time() - elapsed, timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_minutes": round(elapsed / 60, 2),
        "dry_run": dry_run,
        "population": population,
        "targets_total": len(targets),
        "targets_by_population": by_pop,
        "status_counts": counts,
        "old_search_chars_total": total_old,
        "new_search_chars_total": total_new,
        "native_body_chars_total": total_native,
        "multiplier_search": (
            round(total_new / total_old, 2) if total_old else None
        ),
    }
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(
        f"\n[native-reextract] DONE  elapsed={elapsed/60:.1f}min  "
        f"updated={counts.get('updated',0)}  "
        f"skipped(vision-only)={counts.get('skipped-vision-only',0)}  "
        f"missing={counts.get('missing',0)}  "
        f"errors={counts.get('db-error',0)+counts.get('worker-crash',0)}"
    )
    if total_old:
        print(
            f"  search_text growth: {total_old:,} -> {total_new:,} "
            f"({total_new/total_old:.1f}x)"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="Corpus-wide native-text re-extraction")
    ap.add_argument(
        "--population", default="both",
        choices=["wave1", "pillar3", "both"],
        help="which population to re-extract (default: both)",
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="extract + log CSV but do not UPDATE the DB")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--limit", type=int, default=None,
                    help="cap target row count (testing)")
    args = ap.parse_args()
    run(args.population, dry_run=args.dry_run,
        concurrency=args.concurrency, limit=args.limit)


if __name__ == "__main__":
    main()
