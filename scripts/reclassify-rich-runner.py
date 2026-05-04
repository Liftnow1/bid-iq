"""Re-classify rows whose body content was upgraded by native re-extract.

After the native-reextract pass turned a couple thousand Wave 1 rows
from cover-only metadata into full-body content, the v2.1 classifier
gets a much richer signal. Many rows that were `category=['uncategorized']`
on the original Tier-1 ingest can now be confidently sorted into a
real tier.

Targets: rows where category=['uncategorized'] AND extractor_version
is one of the post-native tags (so we know they have rich body content,
not just the original cover-page metadata).

For each, runs `classify_document` against title+summary+body. If the
classifier returns a concrete tier (tier-1-public / tier-2-internal /
tier-3-paul-only), UPDATEs the row's category. If it stays
`uncategorized`, the row is left alone for human review.

Usage:
    python scripts/reclassify-rich-runner.py
    python scripts/reclassify-rich-runner.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
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
    pat = re.compile(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.+?)\s*$")
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            m = pat.match(line)
            if m and not os.environ.get(m.group(1)):
                os.environ[m.group(1)] = m.group(2)


_load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import anthropic  # noqa: E402
import psycopg  # noqa: E402

from bidiq.ingest import classify_document  # noqa: E402


LOG_DIR = REPO_ROOT / "logs"
LOG_PATH = LOG_DIR / "reclassify-rich.csv"

# Rows must have one of these versions to be considered for re-classification.
# Anything else is either pre-native (cover-only) or a different workflow.
ELIGIBLE_VERSIONS = (
    "ingest.py-v1-native",
    "ingest.py-v1-pillar3-full",
    "ingest.py-v1-vision-fallback",
)

CONCRETE_TIERS = {"tier-1-public", "tier-2-internal", "tier-3-paul-only"}


def fetch_targets(conn: psycopg.Connection) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, source_filename, coalesce(title,''), coalesce(summary,''),
                   coalesce(raw_content,''), length(coalesce(raw_content,''))
              FROM knowledge_items
             WHERE 'uncategorized' = ANY(category)
               AND extractor_version = ANY(%s)
             ORDER BY id
            """,
            (list(ELIGIBLE_VERSIONS),),
        )
        return cur.fetchall()


def classify_row(client, model: str, title: str, summary: str, body: str) -> str:
    cats = classify_document(
        client, title=title, summary=summary, body_text=body, model=model
    )
    return cats[0] if cats else "uncategorized"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Classify and log but do not UPDATE the DB")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL") or ""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    if not db_url or not api_key:
        print("ERROR: DATABASE_URL or ANTHROPIC_API_KEY missing", file=sys.stderr)
        sys.exit(2)
    model = "claude-sonnet-4-20250514"

    with psycopg.connect(db_url) as conn:
        targets = fetch_targets(conn)
    print(f"[reclassify-rich] {len(targets)} candidates  dry_run={args.dry_run}")

    client = anthropic.Anthropic(api_key=api_key)

    started = time.time()
    results: list[tuple] = []

    def work(t):
        rid, fn, title, summary, body, n = t
        try:
            new = classify_row(client, model, title, summary, body)
            return rid, fn, n, new, None
        except Exception as e:
            return rid, fn, n, None, f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = {pool.submit(work, t): t for t in targets}
        for i, fut in enumerate(as_completed(futs), 1):
            results.append(fut.result())
            if i % 10 == 0 or i == len(targets):
                print(f"  [{i}/{len(targets)}] {time.time()-started:.1f}s")

    resolved = [r for r in results if r[3] in CONCRETE_TIERS]
    still_uncat = [r for r in results if r[3] == "uncategorized"]
    errors = [r for r in results if r[4]]

    print(f"\n  resolved to a tier:    {len(resolved)}")
    print(f"  still uncategorized:   {len(still_uncat)}")
    print(f"  errors:                {len(errors)}")

    if not args.dry_run and resolved:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                for rid, fn, n, new, _ in resolved:
                    cur.execute(
                        "UPDATE knowledge_items SET category = ARRAY[%s]::text[] "
                        "WHERE id = %s",
                        (new, rid),
                    )
            conn.commit()
        print(f"  UPDATED {len(resolved)} rows.")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "timestamp", "row_id", "filename",
        "old_category", "new_category", "body_chars", "status",
    ]
    with open(LOG_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        ts = datetime.now(timezone.utc).isoformat()
        for rid, fn, n, new, err in results:
            if err:
                status = "error"
            elif new in CONCRETE_TIERS:
                status = "dry-run" if args.dry_run else "updated"
            else:
                status = "still-uncategorized"
            w.writerow({
                "timestamp": ts, "row_id": rid, "filename": fn,
                "old_category": "uncategorized", "new_category": new or "",
                "body_chars": n, "status": status,
            })
    print(f"  log: {LOG_PATH}")


if __name__ == "__main__":
    main()
