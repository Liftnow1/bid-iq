#!/usr/bin/env python3
"""
Cross-link Forward EL/DT variant trims to their base family's docs.

Forward sells the same lift in several trims:
  CR14   <-> CR14-EL    (electric latch release)
  CRA14  <-> CRA14-EL
  CRO14  <-> CRO14-EL2
  1000MCL <-> 1000MCLDT (deep transmission)

The install/parts/spec docs filed under FWD_4PL_CR14_... cover both the
base and the EL trim. The variant rows otherwise have 0 docs because the
filenames never spell out "CR14-EL". Copy each base family's docs onto
its trim sibling so the catalog reflects reality.

Inserts are dedup'd against existing (product, ki, doctype) triples,
and is_primary is recomputed per (product, doc_type) afterward.
"""

from __future__ import annotations
import os
import sys
from pathlib import Path

import psycopg  # type: ignore
from psycopg.rows import dict_row  # type: ignore

DB_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
if not DB_URL:
    for envfile in (".env.local", ".env"):
        env_path = Path(__file__).resolve().parent.parent / envfile
        if not env_path.exists():
            continue
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if "=" not in line or line.startswith("#"):
                continue
            k, v = line.split("=", 1)
            if k.strip() in ("DATABASE_URL", "POSTGRES_URL"):
                DB_URL = v.strip().strip('"').strip("'")
                break
        if DB_URL:
            break

if not DB_URL:
    print("ERROR: DATABASE_URL not set", file=sys.stderr)
    sys.exit(1)

# (base_sku, trim_sku) pairs to cross-link (Forward brand)
PAIRS = [
    ("CR14", "CR14-EL"),
    ("CRA14", "CRA14-EL"),
    ("CRO14", "CRO14-EL2"),
    ("1000MCL", "1000MCLDT"),
]


def main():
    dry_run = "--dry-run" in sys.argv[1:]

    conn = psycopg.connect(DB_URL, autocommit=False)
    cur = conn.cursor(row_factory=dict_row)

    cur.execute("""
        SELECT p.id, p.sku FROM products p JOIN brands b ON b.id = p.brand_id
        WHERE lower(b.name) = 'forward'
    """)
    sku_to_pid = {r["sku"]: r["id"] for r in cur.fetchall()}

    total_inserts = 0
    for base_sku, trim_sku in PAIRS:
        base_pid = sku_to_pid.get(base_sku)
        trim_pid = sku_to_pid.get(trim_sku)
        if not base_pid or not trim_pid:
            print(f"  SKIP {base_sku} -> {trim_sku} (missing in DB)")
            continue

        cur.execute("""
            SELECT pd.knowledge_item_id, pd.doc_type, pd.notes
            FROM product_documents pd
            WHERE pd.product_id = %s
        """, (base_pid,))
        base_docs = cur.fetchall()
        if not base_docs:
            print(f"  {base_sku} -> {trim_sku}: base has no docs, nothing to copy")
            continue

        inserted = 0
        skipped = 0
        for d in base_docs:
            cur.execute("""
                SELECT 1 FROM product_documents
                WHERE product_id = %s AND knowledge_item_id = %s AND doc_type = %s
                LIMIT 1
            """, (trim_pid, d["knowledge_item_id"], d["doc_type"]))
            if cur.fetchone():
                skipped += 1
                continue
            note = (d["notes"] or "")
            cross_note = f"cross-linked from {base_sku}"
            new_note = (note + "; " if note else "") + cross_note
            cur.execute("""
                INSERT INTO product_documents
                  (product_id, knowledge_item_id, doc_type, is_primary, notes)
                VALUES (%s, %s, %s, false, %s)
            """, (trim_pid, d["knowledge_item_id"], d["doc_type"], new_note))
            inserted += 1

        print(f"  {base_sku} -> {trim_sku}: {inserted} inserted, {skipped} already linked")
        total_inserts += inserted

    # Recompute is_primary for all touched trim products + their bases
    touched_pids = []
    for base_sku, trim_sku in PAIRS:
        for s in (base_sku, trim_sku):
            if s in sku_to_pid:
                touched_pids.append(sku_to_pid[s])

    if touched_pids:
        cur.execute("""
            WITH ranked AS (
                SELECT pd.id,
                       row_number() OVER (
                           PARTITION BY pd.product_id, pd.doc_type
                           ORDER BY length(coalesce(ki.raw_content,'')) DESC, pd.id
                       ) AS rn
                FROM product_documents pd
                LEFT JOIN knowledge_items ki ON ki.id = pd.knowledge_item_id
                WHERE pd.product_id = ANY(%s)
            )
            UPDATE product_documents pd
            SET is_primary = (ranked.rn = 1)
            FROM ranked
            WHERE pd.id = ranked.id
        """, (touched_pids,))

    if dry_run:
        conn.rollback()
        print(f"\n[DRY RUN] Would have inserted {total_inserts} cross-links. Rolled back.")
    else:
        conn.commit()
        print(f"\nCross-linked {total_inserts} docs across EL/DT variants.")

    # Final Forward coverage
    cur.execute("""
        SELECT count(DISTINCT p.id) AS with_docs,
               (SELECT count(*) FROM products p2 JOIN brands b2 ON b2.id = p2.brand_id
                WHERE lower(b2.name) = 'forward') AS total
        FROM products p
        JOIN brands b ON b.id = p.brand_id
        JOIN product_documents pd ON pd.product_id = p.id
        WHERE lower(b.name) = 'forward'
    """)
    r = cur.fetchone()
    pct = (100 * r["with_docs"] // r["total"]) if r["total"] else 0
    print(f"Forward coverage: {r['with_docs']}/{r['total']} ({pct}%)")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
