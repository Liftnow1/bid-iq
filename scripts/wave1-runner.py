"""Wave 1 KB ingest runner.

Wraps bidiq.ingest.process_single_pdf with structured CSV logging and a
running summary JSON, one carry brand per invocation.

Usage:
    python scripts/wave1-runner.py --brand pks
    python scripts/wave1-runner.py --brand pks --concurrency 3 --limit 5

Outputs:
    logs/wave1-ingest.csv     append-mode, one row per PDF processed
    logs/wave1-summary.json   rewritten after each brand completes
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

# Force UTF-8 stdout/stderr so non-cp1252 filenames (e.g. embedded
# zero-width or hangul-filler chars) don't crash print().
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import psycopg  # noqa: E402

from bidiq.ingest import (  # noqa: E402
    discover_pdfs,
    ensure_brand,
    process_single_pdf,
    repo_relative_path,
)

LOGS_DIR = REPO_ROOT / "logs"
CSV_PATH = LOGS_DIR / "wave1-ingest.csv"
SUMMARY_PATH = LOGS_DIR / "wave1-summary.json"

CSV_FIELDS = [
    "timestamp",
    "brand",
    "filename",
    "file_size_mb",
    "pages",
    "extraction_chars",
    "category_assigned",
    "status",
    "error_message",
    "db_row_id",
    "api_cost_estimate",
]

# Calibrated to brief's $20-30 estimate over ~2,000 tier-1 PDFs.
# Tier-1 = 2-image shallow extract + small classify call.
COST_PER_PDF_USD = 0.013

OK_RE = re.compile(
    r"\bOK\s+.*?\((\d+)p,.*?knowledge_items\.id=(\d+)\s*\[(INSERT|UPDATE)\]"
)
# Two FAIL forms: "<path> - <ErrType>: <msg>" (exception) and
# "<path> - 0 pages rendered" (no-pages bail-out, no colon).
FAIL_TYPED_RE = re.compile(r"\bFAIL\s+.*?-\s*(\w+):\s*(.*)$")
FAIL_PLAIN_RE = re.compile(r"\bFAIL\s+.*?-\s*(.*)$")
SKIP_RE = re.compile(r"\bSKIP\s+.*?\((.+)\)\s*$")


def parse_result_line(line: str) -> dict:
    line = line.rstrip()
    m = OK_RE.search(line)
    if m:
        return {
            "status": "ingested",
            "pages": int(m.group(1)),
            "db_row_id": int(m.group(2)),
            "action": m.group(3),
        }
    m = FAIL_TYPED_RE.search(line)
    if m:
        return {
            "status": "failed",
            "error_type": m.group(1),
            "error_message": m.group(2),
        }
    m = FAIL_PLAIN_RE.search(line)
    if m:
        return {
            "status": "failed",
            "error_type": "unknown",
            "error_message": m.group(1),
        }
    m = SKIP_RE.search(line)
    if m:
        return {"status": "skipped", "reason": m.group(1)}
    # Unrecognized — keep the raw line for forensics.
    return {"status": "unknown", "raw": line, "error_message": line.strip()}


def query_row_meta(conn, row_id: int) -> tuple[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT category, length(coalesce(search_text,'')) "
            "FROM knowledge_items WHERE id = %s",
            (row_id,),
        )
        r = cur.fetchone()
        if not r:
            return "", 0
        cat, slen = r
        if isinstance(cat, list):
            cat_str = ",".join(cat)
        else:
            cat_str = str(cat) if cat is not None else ""
        return cat_str, int(slen or 0)


def ensure_csv_header() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if not CSV_PATH.exists():
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()


def append_csv_row(row: dict) -> None:
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)


def load_summary() -> dict:
    if SUMMARY_PATH.exists():
        with open(SUMMARY_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "brands_completed": [],
        "per_brand": {},
        "totals": {
            "files_processed": 0,
            "files_ingested": 0,
            "files_skipped": 0,
            "files_failed": 0,
            "tier_distribution": {},
            "estimated_cost_usd": 0.0,
        },
    }


def write_summary(data: dict, brand_summary: dict) -> None:
    data["per_brand"][brand_summary["brand"]] = brand_summary
    if brand_summary["brand"] not in data["brands_completed"]:
        data["brands_completed"].append(brand_summary["brand"])
    data["last_updated"] = datetime.now(timezone.utc).isoformat()

    totals = {
        "files_processed": 0,
        "files_ingested": 0,
        "files_skipped": 0,
        "files_failed": 0,
        "tier_distribution": {},
        "estimated_cost_usd": 0.0,
    }
    for b in data["per_brand"].values():
        totals["files_processed"] += b.get("processed", 0)
        totals["files_ingested"] += b.get("ingested", 0)
        totals["files_skipped"] += b.get("skipped", 0)
        totals["files_failed"] += b.get("failed", 0)
        totals["estimated_cost_usd"] += b.get("estimated_cost_usd", 0.0)
        for k, v in (b.get("tier_distribution") or {}).items():
            totals["tier_distribution"][k] = (
                totals["tier_distribution"].get(k, 0) + v
            )
    totals["estimated_cost_usd"] = round(totals["estimated_cost_usd"], 4)
    data["totals"] = totals

    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def run_brand(brand: str, concurrency: int = 3, limit: int | None = None) -> dict:
    pdfs = discover_pdfs(brand)
    if limit is not None:
        pdfs = pdfs[:limit]
    print(
        f"[{brand}] discovered {len(pdfs)} PDFs "
        f"(concurrency={concurrency}, limit={limit})"
    )

    db_url = os.environ["DATABASE_URL"]
    api_key = os.environ["ANTHROPIC_API_KEY"]
    model = "claude-sonnet-4-20250514"

    with psycopg.connect(db_url) as conn:
        ensure_brand(conn, brand)

    ensure_csv_header()
    started = time.time()
    counts = {"ingested": 0, "skipped": 0, "failed": 0}
    tier_dist: dict[str, int] = {}
    consecutive_failures = 0

    size_map: dict[str, float] = {}
    for p in pdfs:
        try:
            size_map[repo_relative_path(p)] = round(
                p.stat().st_size / 1024 / 1024, 3
            )
        except OSError:
            size_map[repo_relative_path(p)] = 0.0

    def work(pdf: Path) -> tuple[Path, str]:
        line = process_single_pdf(pdf, db_url, api_key, model, 130, 1)
        return pdf, line

    failed_lines: list[str] = []

    def record(pdf: Path, line: str, parsed: dict) -> None:
        """Append a CSV row + update brand-level counters."""
        nonlocal consecutive_failures
        ts = datetime.now(timezone.utc).isoformat()
        row: dict = {
            "timestamp": ts,
            "brand": brand,
            "filename": pdf.name,
            "file_size_mb": size_map.get(repo_relative_path(pdf), 0.0),
            "pages": parsed.get("pages", ""),
            "extraction_chars": "",
            "category_assigned": "",
            "status": parsed["status"],
            "error_message": parsed.get("error_message", ""),
            "db_row_id": parsed.get("db_row_id", ""),
            "api_cost_estimate": (
                COST_PER_PDF_USD if parsed["status"] == "ingested" else 0
            ),
        }
        if parsed["status"] == "ingested":
            counts["ingested"] += 1
            consecutive_failures = 0
            try:
                with psycopg.connect(db_url) as conn:
                    cat, slen = query_row_meta(conn, parsed["db_row_id"])
                row["extraction_chars"] = slen
                row["category_assigned"] = cat
                if cat:
                    for c in cat.split(","):
                        ck = c.strip()
                        if ck:
                            tier_dist[ck] = tier_dist.get(ck, 0) + 1
            except Exception as e:
                row["error_message"] = (
                    f"db meta lookup failed: {type(e).__name__}: {e}"
                )
        elif parsed["status"] == "skipped":
            counts["skipped"] += 1
            consecutive_failures = 0
        else:
            counts["failed"] += 1
            consecutive_failures += 1
            failed_lines.append(line.strip())
        append_csv_row(row)
        done = counts["ingested"] + counts["skipped"] + counts["failed"]
        el = time.time() - started
        print(
            f"  [{done}/{len(pdfs)}] {parsed['status']:9s} "
            f"({el:5.1f}s) {pdf.name}"
        )

    pending_retries: list[Path] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = {pool.submit(work, pdf): pdf for pdf in pdfs}
        for fut in as_completed(futs):
            try:
                pdf, line = fut.result()
            except Exception as e:
                pdf = futs[fut]
                line = (
                    f"  FAIL  {repo_relative_path(pdf)} - "
                    f"{type(e).__name__}: {e}"
                )
            parsed = parse_result_line(line)
            # Transient failures get one retry — pool startup occasionally
            # hits the first 1-2 PDFs with a fast, recoverable error.
            if parsed["status"] in ("failed", "unknown"):
                pending_retries.append(pdf)
                continue
            record(pdf, line, parsed)
            if consecutive_failures >= 5:
                print(
                    f"  STOP  5 consecutive failures — halting {brand}.",
                    file=sys.stderr,
                )
                for fl in failed_lines[-5:]:
                    print(f"    {fl}", file=sys.stderr)
                break

    if pending_retries:
        print(
            f"[{brand}] retrying {len(pending_retries)} "
            f"failed/unknown PDFs (single-threaded)..."
        )
        for pdf in pending_retries:
            _, line = work(pdf)
            parsed = parse_result_line(line)
            record(pdf, line, parsed)
            if consecutive_failures >= 5:
                print(
                    f"  STOP  5 consecutive failures in retry pass.",
                    file=sys.stderr,
                )
                break

    elapsed = time.time() - started
    summary = {
        "brand": brand,
        "discovered": len(pdfs),
        "processed": counts["ingested"]
        + counts["skipped"]
        + counts["failed"],
        "ingested": counts["ingested"],
        "skipped": counts["skipped"],
        "failed": counts["failed"],
        "tier_distribution": tier_dist,
        "estimated_cost_usd": round(
            counts["ingested"] * COST_PER_PDF_USD, 4
        ),
        "elapsed_minutes": round(elapsed / 60, 2),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    data = load_summary()
    write_summary(data, summary)

    print(
        f"[{brand}] DONE "
        f"ingested={counts['ingested']} skipped={counts['skipped']} "
        f"failed={counts['failed']} "
        f"tier_dist={tier_dist} "
        f"elapsed={elapsed/60:.1f}min "
        f"~${summary['estimated_cost_usd']:.2f}"
    )
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Wave 1 ingest runner")
    ap.add_argument("--brand", required=True, help="brand folder name")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument(
        "--limit", type=int, default=None, help="cap PDFs (testing)"
    )
    args = ap.parse_args()

    if not os.environ.get("DATABASE_URL") or not os.environ.get(
        "ANTHROPIC_API_KEY"
    ):
        print(
            "Error: DATABASE_URL or ANTHROPIC_API_KEY not set",
            file=sys.stderr,
        )
        sys.exit(2)

    run_brand(args.brand, args.concurrency, args.limit)


if __name__ == "__main__":
    main()
