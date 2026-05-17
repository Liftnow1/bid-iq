#!/usr/bin/env python3
"""
Generate sample API response JSONs straight from the live DB, mirroring
the queries in app/api/products/{route.ts, [id]/route.ts, [id]/documents/route.ts}.

These samples ship in handoff/sample-responses/ so the portal team's Claude
can see real response shapes without having to call the live API at
https://bid-iq-neon.vercel.app.

Outputs:
  products-page1.json
  product-detail.json
  product-documents.json
  product-include-documents.json   (the same page1 query but with documents inlined)
"""

from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from datetime import date, datetime
from decimal import Decimal

import psycopg  # type: ignore
from psycopg.rows import dict_row  # type: ignore

DB_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
if not DB_URL:
    for envfile in (".env.local", ".env"):
        env_path = Path(__file__).resolve().parents[2] / envfile
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

OUT_DIR = Path(__file__).resolve().parent


def to_jsonable(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(f"not jsonable: {type(o)}")


def main():
    conn = psycopg.connect(DB_URL)
    cur = conn.cursor(row_factory=dict_row)

    # ---- products page 1, 3-row sample ----
    cur.execute("""
        SELECT count(*)::int AS n
        FROM products p JOIN brands b ON b.id = p.brand_id
    """)
    total = cur.fetchone()["n"]

    cur.execute("""
        SELECT p.id, p.brand_id, b.name AS brand_name, p.sku, p.family_name,
               p.product_name, p.description, p.category, p.capacity_lbs,
               p.is_ali_certified, p.ali_cert_date,
               coalesce(p.variant_skus, '[]'::jsonb) AS variant_skus,
               p.status, p.source, p.source_file, p.notes,
               p.image_url, p.image_source_url, p.image_fetched_at,
               p.created_at, p.updated_at
        FROM products p JOIN brands b ON b.id = p.brand_id
        ORDER BY b.name, p.category, p.capacity_lbs NULLS LAST, p.sku
        LIMIT 3
    """)
    rows = cur.fetchall()

    page_payload = {
        "products": rows,
        "total": total,
        "page": 1,
        "page_size": 3,
        "total_pages": (total + 2) // 3,
    }
    (OUT_DIR / "products-page1.json").write_text(
        json.dumps(page_payload, default=to_jsonable, indent=2)
    )

    # ---- single product detail (pick a richly-documented one) ----
    cur.execute("""
        SELECT p.id, count(pd.id) AS doc_count
        FROM products p LEFT JOIN product_documents pd ON pd.product_id = p.id
        GROUP BY p.id
        ORDER BY count(pd.id) DESC, p.id
        LIMIT 1
    """)
    pid = cur.fetchone()["id"]

    cur.execute("""
        SELECT p.id, p.brand_id, b.name AS brand_name, p.sku, p.family_name,
               p.product_name, p.description, p.category, p.capacity_lbs,
               p.is_ali_certified, p.ali_cert_date,
               coalesce(p.variant_skus, '[]'::jsonb) AS variant_skus,
               p.status, p.source, p.source_file, p.notes,
               p.image_url, p.image_source_url, p.image_fetched_at,
               p.created_at, p.updated_at
        FROM products p JOIN brands b ON b.id = p.brand_id
        WHERE p.id = %s
    """, (pid,))
    product = cur.fetchone()

    cur.execute("""
        SELECT pd.id, pd.product_id, pd.knowledge_item_id, pd.doc_type,
               pd.is_primary, pd.pdf_url, pd.notes, pd.created_at,
               ki.title AS ki_title, ki.source_filename AS ki_filename,
               ki.source_path AS ki_source_path,
               length(coalesce(ki.raw_content,'')) AS ki_body_chars
        FROM product_documents pd
        LEFT JOIN knowledge_items ki ON ki.id = pd.knowledge_item_id
        WHERE pd.product_id = %s
        ORDER BY pd.is_primary DESC, pd.doc_type, pd.id
        LIMIT 5
    """, (pid,))
    docs = cur.fetchall()

    (OUT_DIR / "product-detail.json").write_text(
        json.dumps({"product": product, "documents": docs}, default=to_jsonable, indent=2)
    )

    # ---- /api/products/[id]/documents ----
    (OUT_DIR / "product-documents.json").write_text(
        json.dumps({"documents": docs}, default=to_jsonable, indent=2)
    )

    # ---- include_documents=true variant on page 1 ----
    cur.execute("""
        SELECT p.id, p.brand_id, b.name AS brand_name, p.sku, p.family_name,
               p.product_name, p.description, p.category, p.capacity_lbs,
               p.is_ali_certified, p.ali_cert_date,
               coalesce(p.variant_skus, '[]'::jsonb) AS variant_skus,
               p.status, p.source, p.source_file, p.notes,
               p.image_url, p.image_source_url, p.image_fetched_at,
               p.created_at, p.updated_at
        FROM products p JOIN brands b ON b.id = p.brand_id
        ORDER BY b.name, p.category, p.capacity_lbs NULLS LAST, p.sku
        LIMIT 3
    """)
    rows2 = cur.fetchall()
    ids = [r["id"] for r in rows2]
    cur.execute("""
        SELECT pd.id, pd.product_id, pd.knowledge_item_id, pd.doc_type,
               pd.is_primary, pd.pdf_url,
               ki.title AS ki_title, ki.source_filename AS ki_filename
        FROM product_documents pd
        LEFT JOIN knowledge_items ki ON ki.id = pd.knowledge_item_id
        WHERE pd.product_id = ANY(%s::int[])
        ORDER BY pd.product_id, pd.is_primary DESC, pd.doc_type, pd.id
    """, (ids,))
    by_pid: dict[int, list] = {}
    for d in cur.fetchall():
        by_pid.setdefault(d["product_id"], []).append(d)
    for r in rows2:
        r["documents"] = by_pid.get(r["id"], [])

    inline_payload = {
        "products": rows2,
        "total": total,
        "page": 1,
        "page_size": 3,
        "total_pages": (total + 2) // 3,
    }
    (OUT_DIR / "products-include-documents.json").write_text(
        json.dumps(inline_payload, default=to_jsonable, indent=2)
    )

    print(f"Wrote 4 sample JSONs to {OUT_DIR}")
    print(f"  products-page1.json           — total={total}, 3 rows")
    print(f"  product-detail.json           — product id={pid} with up to 5 docs")
    print(f"  product-documents.json        — same docs, no product wrapper")
    print(f"  products-include-documents.json — page 1 with documents inlined")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
