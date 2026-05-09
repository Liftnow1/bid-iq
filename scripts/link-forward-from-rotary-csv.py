#!/usr/bin/env python3
"""
Link Forward Lift documents to Forward products by parsing
data/product_data/rotary/rotary_lookup.csv.

Forward Lift is a Rotary brand and shares Rotary's S3 hosting. The lookup
CSV maps Rotary's hashed S3 filenames (which we ingested into the KB under
brand 'rotary') to original filenames that start with FWD_ or Forward.

Two filename patterns:
  1. FWD_<CATEGORY>_<SKU(s)>_<DOCTYPE>_<PARTNUM>.pdf
     e.g. FWD_2PL_F10_I10_INSTALL_IN60026E.pdf
  2. Forward <SKU> <DOCTYPE>.<DATE>.pdf
     e.g. Forward I12 CUTSHEET (1).pdf

Multi-SKU filenames (F10_I10) link the same doc to BOTH products.

The user's rule: manuals not brochures. So we skip:
  - "BROCHURE", "COMP CHART", "PRICE SHEET", "CATALOG", "PRODUCT I VIEW"
"""

from __future__ import annotations
import csv
import os
import re
import sys
from urllib.parse import unquote
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
            k = k.strip()
            if k in ("DATABASE_URL", "POSTGRES_URL"):
                DB_URL = v.strip().strip('"').strip("'")
                break
        if DB_URL:
            break

if not DB_URL:
    print("ERROR: DATABASE_URL not set", file=sys.stderr)
    sys.exit(1)

CSV_PATH = Path("data/product_data/rotary/rotary_lookup.csv")

# Filename keywords that classify the doc_type. Order matters - first match wins.
DOCTYPE_MAP = [
    ("INSTALL", "install-manual"),
    ("MANUAL", "install-manual"),
    ("OWNER", "install-manual"),
    ("PARTS-BREAKDOWN", "parts-diagram"),
    ("PARTS BREAKDOWN", "parts-diagram"),
    ("PARTS", "parts-diagram"),
    ("SERVICE", "service-manual"),
    ("SPEC-SHEET", "spec-sheet"),
    ("CUTSHEET", "spec-sheet"),
    ("ACCESSORIES-LIST", "other"),
    ("ACCESSORIES", "other"),
    ("ACCESSORY", "other"),
]

# Skip these - not manuals/specs we want
SKIP_KEYWORDS = [
    "BROCHURE", "COMP CHART", "PRICE SHEET", "CATALOG",
    "I VIEW", "WARRANTY",
]


def classify_doctype(name_upper):
    for kw, dt in DOCTYPE_MAP:
        if kw in name_upper:
            return dt
    return None


def should_skip(name_upper):
    return any(kw in name_upper for kw in SKIP_KEYWORDS)


# Tokens that aren't SKU candidates
NON_SKU_TOKENS = {
    "FWD", "FORWARD", "2PL", "4PL", "MR", "MCL", "SC", "TWO", "FOUR",
    "POST", "POSTS", "2POST", "4POST", "2-POST", "4-POST",
    "LIFT", "LIFTS", "PRO", "CUTSHEET", "MANUAL",
    "INSTALL", "PARTS", "BREAKDOWN", "SERVICE", "OWNER", "ACCESSORIES",
    "ACCESSORY", "LIST", "WARRANTY", "BROCHURE", "PRICE", "SHEET",
    "CATALOG", "MATERIAL", "HANDLING", "PRODUCTS", "PRODUCT",
    "VIEW", "AND", "OR", "OF",
}


def extract_sku_candidates(name):
    """Pull plausible SKU tokens out of a filename."""
    base = name.rsplit(".", 1)[0]
    base = base.replace("(", " ").replace(")", " ")
    upper = base.upper()

    # FWD_<CATEGORY>_<SKU(s)>_<DOCTYPE>_<...>
    # or fallback: FWD_<SKU(s)>_<DOCTYPE>_<...> (no category)
    m = re.match(r"^FWD[_-]+(?:(?:2PL|4PL|MR|MCL|SC)[_-]+)?(.+)", upper)
    if m:
        rest = m.group(1)
        tokens = re.split(r"[_\s]+", rest)
        raw_skus = []
        for t in tokens:
            t = t.strip()
            if not t:
                continue
            # If the token starts a doctype keyword, stop reading SKU candidates.
            if any(t.startswith(kw) or t == kw for kw, _ in DOCTYPE_MAP):
                break
            if t in NON_SKU_TOKENS:
                continue
            raw_skus.append(t)
        # Expand hyphen-joined multi-SKU candidates: DP15-DP18 -> [DP15, DP18]
        skus = []
        for s in raw_skus:
            if "-" in s and not re.fullmatch(r"[A-Z]+\d*-[A-Z]+\d*", s.strip()) is None:
                # split a token like DP15-DP18 or CRO14-OR14
                parts = s.split("-")
                if all(re.match(r"^[A-Z]+\d", p) for p in parts):
                    skus.extend(parts)
                    continue
            skus.append(s)
        return skus

    # Forward <SKU> <something>
    m = re.match(r"^FORWARD\s+(.+)", upper)
    if m:
        rest = m.group(1)
        tokens = re.split(r"[\s_\.]+", rest)
        skus = []
        for t in tokens:
            t = t.strip()
            if not t:
                continue
            if any(t == kw or t.startswith(kw) for kw, _ in DOCTYPE_MAP):
                break
            if t in NON_SKU_TOKENS:
                continue
            if re.fullmatch(r"\d{4}\.\d{2}", t) or re.fullmatch(r"\d{2}\.\d{2}\.\d{2}", t):
                break
            if re.fullmatch(r"\d+", t):
                continue
            skus.append(t)
            break
        return skus

    return []


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args

    conn = psycopg.connect(DB_URL, autocommit=False)
    cur = conn.cursor(row_factory=dict_row)

    cur.execute("""
        SELECT p.id, p.sku, p.family_name,
               coalesce(p.variant_skus, '[]'::jsonb) AS variant_skus
        FROM products p
        JOIN brands b ON b.id = p.brand_id
        WHERE lower(b.name) = 'forward'
    """)
    products = cur.fetchall()
    print(f"Forward products in DB: {len(products)}")

    sku_to_pid = {}
    family_skus = []
    for p in products:
        for s in [p["sku"], *(p["variant_skus"] or [])]:
            su = s.upper()
            sku_to_pid[su] = p["id"]
            family_skus.append((su, p["id"]))
    family_skus.sort(key=lambda x: len(x[0]), reverse=True)

    print(f"Forward SKU index entries: {len(sku_to_pid)}")
    fam_skus_sorted = sorted({p["sku"] for p in products})
    print(f"Forward family SKUs: {fam_skus_sorted}")

    def match_sku(candidate):
        cu = candidate.upper()
        if cu in sku_to_pid:
            return sku_to_pid[cu]
        for sku, pid in family_skus:
            if sku.startswith(cu) and len(cu) >= 2:
                return pid
        return None

    rows = []
    with open(CSV_PATH, encoding="utf-8") as f:
        rd = csv.DictReader(f)
        for r in rd:
            name = unquote(r["original_name"])
            up = name.upper()
            if up.startswith("FWD") or up.startswith("FORWARD"):
                rows.append((r["key_filename"], name))

    print(f"\nForward CSV rows: {len(rows)}")

    cur.execute("""
        SELECT ki.id, ki.source_filename
        FROM knowledge_items ki
        JOIN brands b ON b.id = ki.brand_id
        WHERE lower(b.name) = 'rotary'
    """)
    kb_by_fname = {row["source_filename"]: row["id"] for row in cur.fetchall()}
    print(f"Rotary KB items: {len(kb_by_fname)}")

    skipped_brochure = 0
    no_doctype = 0
    no_sku = 0
    not_in_kb = 0
    insert_pairs = set()
    unmatched_samples = []

    for key_fname, original in rows:
        up = original.upper()

        if should_skip(up):
            skipped_brochure += 1
            continue

        doctype = classify_doctype(up)
        if not doctype:
            no_doctype += 1
            unmatched_samples.append(f"NO-DOCTYPE: {original}")
            continue

        ki_id = kb_by_fname.get(key_fname)
        if not ki_id:
            not_in_kb += 1
            unmatched_samples.append(f"NOT-IN-KB: {key_fname} -> {original}")
            continue

        candidates = extract_sku_candidates(original)
        matched_pids = []
        for c in candidates:
            pid = match_sku(c)
            if pid is not None and pid not in matched_pids:
                matched_pids.append(pid)

        if not matched_pids:
            no_sku += 1
            unmatched_samples.append(
                f"NO-SKU: {original} (candidates: {candidates})"
            )
            continue

        for pid in matched_pids:
            insert_pairs.add((pid, ki_id, doctype))

    print(f"\nResults:")
    print(f"  Skipped (brochure/comp/etc): {skipped_brochure}")
    print(f"  No doc-type detected:        {no_doctype}")
    print(f"  Not in KB (rotary brand):    {not_in_kb}")
    print(f"  No matching Forward SKU:     {no_sku}")
    print(f"  Unique (product, ki, type) inserts queued: {len(insert_pairs)}")

    if unmatched_samples:
        print(f"\nUnmatched samples ({min(len(unmatched_samples), 30)} of {len(unmatched_samples)}):")
        for s in unmatched_samples[:30]:
            print(f"  {s}")

    if dry_run:
        print("\n[DRY RUN] No inserts performed.")
        return

    inserted = 0
    skipped_existing = 0
    for pid, ki_id, doctype in insert_pairs:
        cur.execute("""
            SELECT 1 FROM product_documents
            WHERE product_id = %s AND knowledge_item_id = %s AND doc_type = %s
            LIMIT 1
        """, (pid, ki_id, doctype))
        if cur.fetchone():
            skipped_existing += 1
            continue
        cur.execute("""
            INSERT INTO product_documents (product_id, knowledge_item_id, doc_type, is_primary)
            VALUES (%s, %s, %s, false)
        """, (pid, ki_id, doctype))
        inserted += 1

    cur.execute("""
        WITH ranked AS (
            SELECT pd.id,
                   row_number() OVER (
                       PARTITION BY pd.product_id, pd.doc_type
                       ORDER BY length(coalesce(ki.raw_content,'')) DESC, pd.id
                   ) AS rn
            FROM product_documents pd
            LEFT JOIN knowledge_items ki ON ki.id = pd.knowledge_item_id
            WHERE pd.product_id IN (
                SELECT p.id FROM products p JOIN brands b ON b.id = p.brand_id
                WHERE lower(b.name) = 'forward'
            )
        )
        UPDATE product_documents pd
        SET is_primary = (ranked.rn = 1)
        FROM ranked
        WHERE pd.id = ranked.id
    """)

    conn.commit()
    print(f"\nInserted: {inserted}")
    print(f"Skipped (already existed): {skipped_existing}")

    cur.execute("""
        SELECT count(DISTINCT p.id) AS products_with_docs,
               (SELECT count(*) FROM products p2 JOIN brands b2 ON b2.id = p2.brand_id
                WHERE lower(b2.name) = 'forward') AS total
        FROM products p
        JOIN brands b ON b.id = p.brand_id
        JOIN product_documents pd ON pd.product_id = p.id
        WHERE lower(b.name) = 'forward'
    """)
    res = cur.fetchone()
    pwd = res["products_with_docs"]
    tot = res["total"]
    pct = (100 * pwd // tot) if tot else 0
    print(f"\nForward coverage: {pwd}/{tot} ({pct}%)")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
