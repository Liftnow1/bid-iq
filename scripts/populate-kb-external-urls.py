#!/usr/bin/env python3
"""
Populate knowledge_items.external_url from rotary_lookup.csv.

Rotary Lift (and its sub-brand Forward) publish every product PDF at
https://s3.amazonaws.com/files.appdataroom.com/vsg/media/<hash>.pdf, and
the mapping from {Rotary's hashed filename} -> {original filename + s3 url}
lives in data/product_data/rotary/rotary_lookup.csv. We ingested those PDFs
into knowledge_items using the hashed filename as source_filename, so the
CSV join is a one-liner.

After this script runs, the /api/documents/[id]/pdf route can 302-redirect
to external_url for any Rotary/Forward doc. For other brands we'll fill
external_url once their PDFs are uploaded to R2.

Idempotent: re-running just refreshes the URL if rotary changes their CSV.
"""

from __future__ import annotations
import csv
import os
import sys
from pathlib import Path

import psycopg  # type: ignore
from psycopg.rows import dict_row  # type: ignore

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = REPO_ROOT / "data" / "product_data" / "rotary" / "rotary_lookup.csv"


def load_db_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if url:
        return url
    for envfile in (".env.local", ".env"):
        ep = REPO_ROOT / envfile
        if not ep.exists():
            continue
        for line in ep.read_text().splitlines():
            line = line.strip()
            if "=" not in line or line.startswith("#"):
                continue
            k, v = line.split("=", 1)
            if k.strip() in ("DATABASE_URL", "POSTGRES_URL"):
                return v.strip().strip('"').strip("'")
    print("ERROR: DATABASE_URL not set", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]

    csv_index: dict[str, str] = {}
    with open(CSV_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            fn = (row.get("key_filename") or "").strip()
            url = (row.get("s3_url") or "").strip()
            if fn and url:
                csv_index[fn] = url
    print(f"CSV entries with URL: {len(csv_index)}")

    conn = psycopg.connect(load_db_url(), autocommit=False)
    cur = conn.cursor(row_factory=dict_row)

    cur.execute("""
        SELECT ki.id, ki.source_filename, ki.external_url
        FROM knowledge_items ki
        JOIN brands b ON b.id = ki.brand_id
        WHERE lower(b.name) IN ('rotary', 'forward')
    """)
    rows = cur.fetchall()
    print(f"Rotary/Forward KB items: {len(rows)}")

    to_set = 0
    to_change = 0
    unmatched = 0
    for r in rows:
        fn = r.get("source_filename") or ""
        new_url = csv_index.get(fn)
        if not new_url:
            unmatched += 1
            continue
        cur_url = r.get("external_url") or ""
        if cur_url == new_url:
            continue
        if cur_url:
            to_change += 1
        else:
            to_set += 1
        if not dry_run:
            cur.execute(
                "UPDATE knowledge_items SET external_url = %s WHERE id = %s",
                (new_url, r["id"]),
            )

    print(f"  to set (was NULL):     {to_set}")
    print(f"  to change (was diff):  {to_change}")
    print(f"  no entry in CSV:       {unmatched}")

    if dry_run:
        conn.rollback()
        print("[DRY RUN] No commit.")
    else:
        conn.commit()
        print("Applied.")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
